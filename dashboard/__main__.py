"""`uv run -m dashboard [--port N] [--db PATH]`: serve the journal dashboard on http://127.0.0.1:8765 (read-only)."""

import argparse
from pathlib import Path

from dashboard import data, status
from dashboard.server import PORT, make_server


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only dashboard over the live paper trade journal (localhost only).")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--db", type=Path, default=data.DEFAULT_DB, help="journal file (default data/live/journal.sqlite3)")
    p.add_argument("--desk-data", type=Path, default=status.DESK_ROOT, help="the desk's data folder, read-only (default ~/trading-desk/data)")
    args = p.parse_args()
    server = make_server(args.db, args.port, desk_root=args.desk_data)
    print(f"Dashboard on http://127.0.0.1:{server.server_address[1]}  (read-only, Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
