#!/usr/bin/env python3

# Copyright (c) 2021, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

r"""
For models with variable-sized inputs you must provide the --input-shape argument so that perf_analyzer knows
what shape tensors to use. For example, for a model that has an input called IMAGE that has shape [ 3, N, M ],
where N and M are variable-size dimensions, to tell perf_analyzer to send batch-size 4 requests of shape [ 3, 224, 224 ]
`--shape IMAGE:3,224,224`.
"""

from typing import Dict, List, Optional

import argparse
import csv
import logging
import os
import sys

from model_navigator.log import set_logger
from model_navigator.model_navigator_exceptions import ModelNavigatorException
from model_navigator.perf_analyzer import PerfAnalyzer, PerfAnalyzerConfig
from model_navigator.toolkit.report import (
    save_results,
    show_results,
    sort_results,
)
from model_navigator.toolkit.warmup import warmup
from model_navigator.triton.client import parse_server_url

LOGGER = logging.getLogger("offline_performance")


def calculate_average_latency(r):
    avg_sum_fields = [
        "Client Send",
        "Network+Server Send/Recv",
        "Server Queue",
        "Server Compute",
        "Server Compute Input",
        "Server Compute Infer",
        "Server Compute Output",
        "Client Recv",
    ]
    avg_latency = sum([int(r.get(f, 0)) for f in avg_sum_fields])

    return avg_latency


def update_performance_data(results: List, batch_size: int, performance_partial_file: str):
    row: Dict = {"batch_size": batch_size}
    with open(performance_partial_file, "r") as csvfile:
        reader = csv.DictReader(csvfile)
        for r in reader:
            avg_latency = calculate_average_latency(r)
            row = {**row, **r, "avg latency": avg_latency}

    results.append(row)


def _parse_batch_sizes(batch_sizes: str):
    batches = batch_sizes.split(sep=",")
    return list(map(lambda x: int(x.strip()), batches))


def offline_performance(
    model_name: str,
    batch_sizes: List[int],
    result_path: str,
    input_shapes: Optional[List[str]] = None,
    profiling_data: str = "random",
    triton_instances: int = 1,
    server_url: str = "http://localhost:8000",
    measurement_window: int = 10000,
    shared_memory: bool = False,
):
    LOGGER.info("====== Static batching analysis start ======")

    protocol, host, port = parse_server_url(server_url)
    results: List[Dict] = list()
    for batch_size in batch_sizes:
        LOGGER.info(f"Running performance tests for batch size: {batch_size}")
        performance_partial_file = f"triton_performance_partial_{batch_size}.csv"

        params = {
            "model-name": model_name,
            "model-version": 1,
            "batch-size": batch_size,
            "max-threads": triton_instances,
            "concurrency": triton_instances,
            "threads": triton_instances,
            "url": f"{host}:{port}",
            "protocol": protocol,
            "input-data": profiling_data,
            "measurement-interval": measurement_window,
            "latency-report-file": performance_partial_file,
            "verbose": True,
        }

        if input_shapes:
            params["shapes"] = input_shapes

        if shared_memory:
            params["shared-memory"] = "cuda"

        perf_config = PerfAnalyzerConfig()
        for param, value in params.items():
            perf_config[param] = value

        perf_analyzer = PerfAnalyzer(perf_config, stream_output=True)
        try:
            perf_analyzer.run()
        except ModelNavigatorException as e:
            LOGGER.error(e.message())
            sys.exit(1)

        update_performance_data(results, batch_size, performance_partial_file)
        os.remove(performance_partial_file)

    results = sort_results(results=results)

    save_results(filename=result_path, data=results)
    show_results(results=results)

    LOGGER.info(f"Performance results for static batching stored in: {result_path}")
    LOGGER.info("====== Analysis done ======")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-name", type=str, required=True, help="Name of the model to test")
    parser.add_argument(
        "--input-data", type=str, required=False, default="random", help="Input data to perform profiling."
    )
    parser.add_argument(
        "--input-shape",
        action="append",
        required=False,
        help="Input data shape in form INPUT_NAME:<full_shape_without_batch_axis>.",
    )
    parser.add_argument("--batch-sizes", type=str, required=True, help="List of batch sizes to tests. Comma separated.")
    parser.add_argument("--result-path", type=str, required=True, help="Path where result file is going to be stored.")
    parser.add_argument("--triton-instances", type=int, default=1, help="Number of Triton Server instances")
    parser.add_argument("--server-url", type=str, required=False, default="localhost", help="Url to Triton server")
    parser.add_argument(
        "--measurement-window", required=False, help="Time which perf_analyzer will wait for results", default=10000
    )
    parser.add_argument(
        "--shared-memory", help="Use shared memory for communication with Triton", action="store_true", default=False
    )

    args = parser.parse_args()
    set_logger()

    warmup(
        server_url=args.server_url,
        model_name=args.model_name,
        batch_sizes=_parse_batch_sizes(args.batch_sizes),
        triton_instances=args.triton_instances,
        profiling_data=args.input_data,
        input_shapes=args.input_shape,
        measurement_window=args.measurement_window,
        shared_memory=args.shared_memory,
    )

    offline_performance(
        server_url=args.server_url,
        model_name=args.model_name,
        batch_sizes=_parse_batch_sizes(args.batch_sizes),
        triton_instances=args.triton_instances,
        profiling_data=args.input_data,
        input_shapes=args.input_shape,
        result_path=args.result_path,
        measurement_window=args.measurement_window,
        shared_memory=args.shared_memory,
    )


if __name__ == "__main__":
    main()
