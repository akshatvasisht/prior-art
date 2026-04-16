"""
Main entry point for running priorart as a module.

Usage:
    python -m priorart find --language python --task "http client"
"""

from .cli import main  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    main()
