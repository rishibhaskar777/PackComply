"""Print a QR code for a URL in the terminal: python -m backend.scripts.print_qr URL

Used by scripts/start-demo.ps1 so a phone can scan the laptop's address. Pure Python (the ``qrcode`` package from
requirements, no image libraries needed); if it is missing the URL is printed on its own and the exit code is 2.
"""
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m backend.scripts.print_qr URL", file=sys.stderr)
        return 1
    url = argv[1]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        import qrcode
    except ImportError:
        print(f"(QR code unavailable: run `pip install -e .[dev]` to install the qrcode package)\n{url}")
        return 2
    code = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(url)
    code.make(fit=True)
    # invert=True draws dark modules as light blocks, which scans correctly on the usual dark terminal background.
    code.print_ascii(invert=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
