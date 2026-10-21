import json
import subprocess


def ask(prompt, schema):
    """Run `claude -p` with a JSON schema and return the structured output as a dict."""
    result = subprocess.run(
        [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["structured_output"]


if __name__ == "__main__":
    schema = {
        "type": "object",
        "properties": {
            "open": {"type": "boolean"},
            "new_york_time": {"type": "string"},
        },
        "required": ["open", "new_york_time"],
    }
    print(ask("Is the US stock market open right now?", schema))
