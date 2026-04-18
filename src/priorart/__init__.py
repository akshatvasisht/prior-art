"""
priorart - Build-vs-borrow intelligence for agentic workflows.

Helps AI agents discover and evaluate open source packages to make
informed decisions about whether to build custom solutions or use
existing libraries.
"""

__version__ = "0.2.0"
__author__ = "Prior Art Contributors"

# Export main components
from .core.find_alternatives import find_alternatives
from .core.ingest_repo import ingest_repo
from .core.inspect import inspect_package

__all__ = ["find_alternatives", "ingest_repo", "inspect_package", "__version__"]
