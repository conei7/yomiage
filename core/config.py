import os

from core.json_io import JsonIO


def load_json_with_private(base_path: str, private_path: str) -> dict:
    data = JsonIO(base_path).read()

    if os.path.exists(private_path):
        private_data = JsonIO(private_path).read()

        if isinstance(private_data, dict):
            merged = data.copy()
            merged.update(private_data)
            return merged

    return data


def choose_runtime_json_path(base_path: str, private_path: str) -> str:
    if os.path.exists(private_path):
        return private_path

    return base_path
