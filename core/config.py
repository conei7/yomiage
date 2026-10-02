import os

from core.json_io import JsonIO


def load_json_with_private(base_path: str, private_path: str) -> dict:
    data = JsonIO(base_path).read()

    if os.path.exists(private_path):
        private_data = JsonIO(private_path).read()

        if isinstance(private_data, dict):
            data = data.copy()
            data.update(private_data)

    # Prefer an ephemeral secret when provided while retaining the private JSON
    # format for backward compatibility and non-secret settings.
    environment_token = os.environ.get("DISCORD_BOT_TOKEN")
    if environment_token:
        data = data.copy()
        data["bot_token"] = environment_token

    return data


def choose_runtime_json_path(base_path: str, private_path: str) -> str:
    """Initialize a private runtime copy and always return its path."""
    private_io = JsonIO(private_path)
    with private_io.transaction():
        if not os.path.exists(private_path):
            private_io.write(JsonIO(base_path).read())
    return private_path
