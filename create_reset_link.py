"""
Convenience alias for  admin_tool.py reset-link .

    .\\venv\\Scripts\\python.exe create_reset_link.py user@example.com
    .\\venv\\Scripts\\python.exe create_reset_link.py --list

Both routes do the same thing through the administrator toolkit, so
the logic lives in exactly one place (admin_tool.py). Everything the
old script accepted (email, --base-url) is forwarded unchanged.
"""

import sys

import admin_tool


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    if argv[0] == "--list":
        return admin_tool.main(["list-resets"])
    return admin_tool.main(["reset-link", *argv])


if __name__ == "__main__":
    sys.exit(main())
