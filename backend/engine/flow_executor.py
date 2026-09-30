"""Dependency-layer execution with bounded workers and copied context variables."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context


def execution_layers(nodes, incoming):
    levels = {}
    layers = []
    ids = {node.id for node in nodes}
    for node in nodes:
        level = 1 + max((levels[edge.source] for edge in incoming[node.id] if edge.source in ids), default=-1)
        levels[node.id] = level
        while len(layers) <= level: layers.append([])
        layers[level].append(node)
    return layers


def execute_layers(nodes, incoming, callback, max_workers):
    """Wait for each dependency layer, then publish results in graph order."""
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='vision-flow') as pool:
        for layer in execution_layers(nodes, incoming):
            if max_workers == 1 or len(layer) == 1:
                yield [(node, callback(node)) for node in layer]
            else:
                futures = [pool.submit(copy_context().run, callback, node) for node in layer]
                yield [(node, future.result()) for node, future in zip(layer, futures)]
