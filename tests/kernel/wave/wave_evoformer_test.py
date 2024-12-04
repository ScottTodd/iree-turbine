# Copyright 2024 The IREE Authors
#
# Licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

import pytest
import torch
from torch.nn import functional as F
import math
import iree.turbine.kernel as tk
import iree.turbine.kernel.lang as tkl
import iree.turbine.kernel.wave as tkw
from iree.turbine.kernel.lang.global_symbols import *
from iree.turbine.kernel.wave.utils import (
    get_default_run_config,
    get_default_arch,
    device_randn,
    device_zeros,
    device_randint,
    get_default_scheduling_params,
)
from iree.turbine.kernel.wave.constraints import MMAType
from iree.turbine.kernel.wave.templates.evoformer import get_evoformer_kernel
from iree.turbine.kernel.lang import DataType
import os

_run_e2e = int(os.environ.get("WAVE_RUN_E2E_TESTS", 0))
require_e2e = pytest.mark.skipif(not _run_e2e, reason="e2e tests are disabled")
require_cdna3 = pytest.mark.skipif(
    "gfx94" not in get_default_arch(), reason="Default device is not CDNA3"
)
# Whether to dump the generated MLIR module.
test_dump_generated_mlir = int(os.environ.get("WAVE_DUMP_MLIR", 0))
# Whether to use scheduling group barriers (needs LLVM fix).
enable_scheduling_barriers = int(os.environ.get("WAVE_USE_SCHED_BARRIERS", 0))

# Add test shapes for validation and performance testing.
perf_test = lambda *a: pytest.param(*a, marks=pytest.mark.perf_only)
default_test_shapes = []
# Order of shapes: (B, BN, K2, H, K1, M, N)
default_test_shapes = [(1, 256, 256, 4, 32, 256, 32), (1, 512, 256, 8, 8, 256, 8)]
default_test_shapes += [perf_test(x) for x in default_test_shapes]
default_tile_sizes = [(1, 1, 32, 1, None, 64, 32)]


# From: https://github.com/microsoft/DeepSpeed/blob/master/tests/unit/ops/deepspeed4science/test_DS4Sci_EvoformerAttention.py
def attention_reference(
    q_input: torch.Tensor,
    k_input: torch.Tensor,
    v_input: torch.Tensor,
    biases: list[torch.Tensor],
    sm_scale: float,
) -> torch.Tensor:
    q = q_input.transpose(-2, -3)
    k = k_input.transpose(-2, -3)
    v = v_input.transpose(-2, -3)
    k_t = k.transpose(-1, -2)
    a = torch.matmul(q, k_t) * sm_scale

    for b in biases:
        a += b

    a = F.softmax(a, dim=-1)
    a_v = torch.matmul(a, v)
    o = a_v.transpose(-2, -3)

    return o


@require_e2e
@pytest.mark.parametrize("shape", default_test_shapes)
@pytest.mark.parametrize("tile_sizes", default_tile_sizes)
@pytest.mark.parametrize("enable_scheduling", [False])
@pytest.mark.parametrize(
    "mfma_variant",
    [
        MMAType.F32_32x32x8_F16,
    ],
)
@pytest.mark.parametrize("dtype", [tkl.f16, tkl.bf16])
def testEvoformerAttentionForward(
    shape: tuple[int],
    tile_sizes: tuple[int],
    enable_scheduling: bool,
    mfma_variant: MMAType,
    dtype: DataType,
    request,
):
    run_bench = request.config.getoption("--runperf")
    dump_perf = request.config.getoption("--dump-perf-files-path")
    shapes_and_tile_sizes = [(x, y) for x, y in zip(shape, tile_sizes)]
    evoformer_fwd, symbols = get_evoformer_kernel(
        *shapes_and_tile_sizes, mfma_variant, dtype
    )

    symbols.update(get_default_scheduling_params())

    config = get_default_run_config()
    if run_bench:
        config["benchmark_batch_size"] = 1000
        config["benchmark_repetitions"] = 3
    if dump_perf is not None:
        perf_filename = request.node.name + ".json"
        config["benchmark_results_file"] = os.path.join(
            dump_perf, "tk_" + perf_filename
        )

    with tk.gen.TestLaunchContext(
        symbols,
        canonicalize=True,
        run=True,
        run_bench=run_bench,
        run_config=config,
        schedule=enable_scheduling,
        use_scheduling_barriers=enable_scheduling_barriers,
    ):
        torch.manual_seed(0)
        if dtype == tkl.bf16:
            torch_dtype = torch.bfloat16
        else:
            torch_dtype = torch.float16
        batch, n, kv_seq_len, heads, head_dim, q_seq_len, v_dim = shape
        q = device_randn(batch, n, q_seq_len, heads, head_dim, dtype=torch_dtype)
        k = device_randn(batch, n, kv_seq_len, heads, head_dim, dtype=torch_dtype)
        v = device_randn(batch, n, kv_seq_len, heads, v_dim, dtype=torch_dtype)
        mask = device_randint(0, 2, (batch, n, kv_seq_len), dtype=torch_dtype)
        mask_bias = 1e9 * (mask - 1)
        bias = device_randn(batch, heads, q_seq_len, kv_seq_len, dtype=torch_dtype)
        output = device_zeros(batch, n, q_seq_len, heads, v_dim, dtype=torch_dtype)
        log2e = 1.44269504089
        dk_sqrt = math.sqrt(1.0 / shape[4])
        # TODO: Add scaling of QK as part of kernel.
        # TODO: Add v-permute as part of kernel.
        mb = evoformer_fwd(
            q * dk_sqrt * log2e,
            k,
            v.permute([0, 1, 4, 3, 2]),
            mask_bias,
            bias * log2e,
            output,
        )

        mask_bias = mask_bias.view([batch, n, 1, 1, kv_seq_len])
        bias = bias.view([batch, 1, heads, q_seq_len, kv_seq_len])
        torch_ref = attention_reference(q, k, v, [mask_bias, bias], dk_sqrt)

        if test_dump_generated_mlir:
            filename = f"wave_evoformer_{'x'.join(map(str, shape))}.mlir"
            with open(filename, "w") as f:
                f.write(mb.module_op.get_asm())

        eps = 1e-2 if output.dtype == torch.float16 else 5e-2
        assert (
            torch.max(torch.abs(torch_ref - output)).item() < eps
        ), f"out eps: {torch.max(torch.abs(torch_ref - output))}"