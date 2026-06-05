"""
AST-based code extraction for repository ingestion.

Extracts public interfaces from source files to fit within character budget.
"""

import ast
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _annotation_str(arg: ast.arg) -> str:
    if not arg.annotation:
        return ""
    try:
        return f": {ast.unparse(arg.annotation)}"
    except Exception:  # pragma: no cover
        return ""


def _default_str(default: ast.expr) -> str:
    try:
        return ast.unparse(default)
    except Exception:  # pragma: no cover
        return "..."


def _format_arguments(args: ast.arguments) -> str:
    """Format an ast.arguments node as a PEP-8-styled parameter list."""
    parts: list[str] = []

    all_positional = args.posonlyargs + args.args
    n_required = len(all_positional) - len(args.defaults)

    for i, arg in enumerate(all_positional):
        s = arg.arg + _annotation_str(arg)
        if i >= n_required:
            d = _default_str(args.defaults[i - n_required])
            s += f" = {d}" if arg.annotation else f"={d}"
        parts.append(s)
        if args.posonlyargs and i == len(args.posonlyargs) - 1:
            parts.append("/")

    if args.vararg:
        parts.append("*" + args.vararg.arg + _annotation_str(args.vararg))
    elif args.kwonlyargs:
        parts.append("*")

    for i, arg in enumerate(args.kwonlyargs):
        s = arg.arg + _annotation_str(arg)
        kw_default = args.kw_defaults[i]
        if kw_default is not None:
            d = _default_str(kw_default)
            s += f" = {d}" if arg.annotation else f"={d}"
        parts.append(s)

    if args.kwarg:
        parts.append("**" + args.kwarg.arg + _annotation_str(args.kwarg))

    return ", ".join(parts)


class InterfaceExtractor:
    """Extracts public interfaces from source code."""

    def extract_python(self, content: str) -> str:
        """Extract Python public interface using AST.

        Args:
            content: Python source code

        Returns:
            Extracted interface (signatures and docstrings)
        """
        try:
            # Strip UTF-8 BOM (﻿); ast.parse rejects it as a non-printable
            # character even though it's a valid encoding marker.
            if content.startswith("﻿"):
                content = content[1:]
            tree = ast.parse(content)
            lines = []

            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.ClassDef):
                    lines.append(self._extract_class(node))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        lines.append(self._extract_function(node))

            return "\n\n".join(lines)

        except SyntaxError as e:
            logger.warning(f"Python syntax error: {e}")
            return self._fallback_extract(content, "python")
        except Exception as e:
            logger.warning(f"Python extraction error: {e}")
            return self._fallback_extract(content, "python")

    def _extract_class(self, node: ast.ClassDef) -> str:
        """Extract class definition with methods.

        Note: nested ``ClassDef`` nodes inside this class body are intentionally
        not recursed into. Public API typically lives at the module/top-class
        level, and including nested helper classes would inflate the extracted
        surface with implementation detail. Only direct ``FunctionDef`` and
        ``AsyncFunctionDef`` children are walked.
        """
        lines = []

        # Class signature
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(ast.unparse(base))

        base_str = f"({', '.join(bases)})" if bases else ""
        lines.append(f"class {node.name}{base_str}:")

        # Class docstring
        docstring = ast.get_docstring(node)
        if docstring:
            lines.append(f'    """{docstring}"""')

        # Public methods
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip private methods
                if not item.name.startswith("_") or item.name in [
                    "__init__",
                    "__str__",
                    "__repr__",
                ]:
                    method_str = self._extract_method(item)
                    lines.append(method_str)

        return "\n".join(lines)

    def _extract_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Extract function signature and docstring."""
        arg_str = _format_arguments(node.args)

        # Return type annotation
        returns = ""
        if node.returns:
            try:
                returns = f" -> {ast.unparse(node.returns)}"
            except Exception:  # pragma: no cover
                pass

        signature = f"def {node.name}({arg_str}){returns}:"

        # Get docstring
        docstring = ast.get_docstring(node)
        if docstring:
            return f'{signature}\n    """{docstring}"""\n    ...'
        else:
            return f"{signature}\n    ..."

    def _extract_method(self, node: ast.FunctionDef) -> str:
        """Extract method signature (indented for class)."""
        func_str = self._extract_function(node)
        # Indent each line
        lines = func_str.split("\n")
        return "\n".join("    " + line if line else "" for line in lines)

    @staticmethod
    def _strip_comments_and_strings(content: str, language: str) -> str:
        r"""Neutralize comments and string literals before regex extraction.

        The per-language regex extractors in this module are line/structure
        oriented and have no notion of lexical context. Without this pre-pass
        they happily match ``// export function foo() {}`` (commented-out code)
        or template-string bodies that contain documentation examples,
        inflating the extracted public surface with dead code.

        The pass preserves byte/line offsets where it matters by keeping empty
        delimiters (``""``, ``''``, ``\`\``) in place so subsequent regexes
        still see syntactically valid source.
        """
        if language not in {"typescript", "javascript", "rust", "go"}:
            return content

        # Order matters: kill block comments first so a ``//`` inside ``/* */``
        # isn't preserved when the block strip runs. Then gut strings (template
        # > double > single) so a ``//`` inside a string literal — e.g. a
        # ``"https://..."`` URL — is neutralized to ``""`` before the line-comment
        # strip can treat it as a comment and swallow the rest of the line.
        content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        content = re.sub(r"`(?:[^`\\]|\\.)*`", "``", content, flags=re.DOTALL)
        content = re.sub(r'"(?:[^"\\]|\\.)*"', '""', content)
        content = re.sub(r"'(?:[^'\\]|\\.)*'", "''", content)
        content = re.sub(r"//[^\n]*", "", content)
        return content

    def extract_typescript(self, content: str) -> str:
        """Extract TypeScript public interface using regex."""
        content = self._strip_comments_and_strings(content, "typescript")
        patterns = [
            # Export functions
            r"export\s+(?:async\s+)?function\s+\w+\s*\([^)]*\)[^{]*",
            # Export classes
            r"export\s+class\s+\w+(?:\s+extends\s+\w+)?(?:\s+implements\s+[\w\s,]+)?\s*\{[^}]*(?:constructor|public)[^}]*\}",
            # Export interfaces
            r"export\s+interface\s+\w+\s*\{[^}]*\}",
            # Export types
            r"export\s+type\s+\w+\s*=[^;]+;",
            # Export const
            r"export\s+const\s+\w+\s*:\s*[^=]+=",
        ]

        extracted = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
            extracted.extend(matches[:10])  # Limit matches per pattern

        return (
            "\n\n".join(extracted) if extracted else self._fallback_extract(content, "typescript")
        )

    def extract_javascript(self, content: str) -> str:
        """Extract JavaScript public interface using regex."""
        content = self._strip_comments_and_strings(content, "javascript")
        patterns = [
            # Export functions (ES6)
            r"export\s+(?:async\s+)?function\s+\w+\s*\([^)]*\)[^{]*",
            # Export arrow functions
            r"export\s+const\s+\w+\s*=\s*(?:async\s*)?\([^)]*\)\s*=>[^;]+;",
            # Export classes
            r"export\s+class\s+\w+(?:\s+extends\s+\w+)?\s*\{[^}]*constructor[^}]*\}",
            # Module exports
            r"module\.exports\s*=\s*\{[^}]+\}",
            # Named exports
            r"exports\.\w+\s*=\s*(?:function|class|\{)[^;]+;",
        ]

        extracted = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
            extracted.extend(matches[:10])

        return (
            "\n\n".join(extracted) if extracted else self._fallback_extract(content, "javascript")
        )

    def extract_rust(self, content: str) -> str:
        """Extract Rust public interface using regex."""
        content = self._strip_comments_and_strings(content, "rust")
        patterns = [
            # Public functions
            r"pub\s+(?:async\s+)?fn\s+\w+[^{]*\{",
            # Public structs
            r"pub\s+struct\s+\w+(?:<[^>]+>)?\s*(?:\{[^}]*\}|\([^)]*\)|;)",
            # Public enums
            r"pub\s+enum\s+\w+(?:<[^>]+>)?\s*\{[^}]*\}",
            # Public traits
            r"pub\s+trait\s+\w+(?:<[^>]+>)?\s*\{[^}]*\}",
            # Public type aliases
            r"pub\s+type\s+\w+\s*=\s*[^;]+;",
            # Public constants
            r"pub\s+const\s+\w+:\s*[^=]+=\s*[^;]+;",
        ]

        extracted = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
            # Clean up matches (remove body, keep signature)
            cleaned = []
            for match in matches[:10]:
                # For functions, remove body
                if "fn " in match:
                    match = match.split("{")[0] + "{ ... }"
                cleaned.append(match)
            extracted.extend(cleaned)

        return "\n\n".join(extracted) if extracted else self._fallback_extract(content, "rust")

    def extract_go(self, content: str) -> str:
        """Extract Go public interface using regex."""
        content = self._strip_comments_and_strings(content, "go")
        patterns = [
            # Public functions (capitalized)
            r"^func\s+[A-Z]\w*\s*\([^)]*\)[^{]*",
            # Public methods
            r"^func\s+\([^)]+\)\s+[A-Z]\w*\s*\([^)]*\)[^{]*",
            # Public types
            r"^type\s+[A-Z]\w*\s+(?:struct|interface)\s*\{[^}]*\}",
            # Public constants
            r"^const\s+[A-Z]\w*\s+[^=]+=",
            # Public variables
            r"^var\s+[A-Z]\w*\s+[^=]+=",
        ]

        extracted = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            extracted.extend(matches[:10])

        return "\n\n".join(extracted) if extracted else self._fallback_extract(content, "go")

    def _fallback_extract(self, content: str, language: str) -> str:
        """Fallback extraction when AST/regex fails."""
        lines = content.split("\n")

        # Take first 50 non-empty, non-comment lines
        extracted = []
        for line in lines[:200]:
            # Skip empty lines
            if not line.strip():
                continue

            # Skip pure comment lines
            if language in ["python"] and line.strip().startswith("#"):
                continue
            if language in ["javascript", "typescript", "rust", "go"] and line.strip().startswith(
                "//"
            ):
                continue

            extracted.append(line)

            if len(extracted) >= 50:
                break

        return "\n".join(extracted)

    def extract(self, file_path: Path, content: str) -> str:
        """Extract interface based on file extension.

        Args:
            file_path: Path to file (for extension detection)
            content: File content

        Returns:
            Extracted interface or truncated content
        """
        # .d.ts must be checked before .suffix since Path("foo.d.ts").suffix == ".ts"
        if file_path.name.endswith(".d.ts"):
            return content

        extension = file_path.suffix.lower()

        extractors = {
            ".py": self.extract_python,
            ".pyi": lambda c: c,  # Type stubs are already interface-only
            ".ts": self.extract_typescript,
            ".tsx": self.extract_typescript,
            ".js": self.extract_javascript,
            ".jsx": self.extract_javascript,
            ".rs": self.extract_rust,
            ".go": self.extract_go,
        }

        extractor = extractors.get(extension)
        if extractor:
            return extractor(content)

        # Unknown type - return first part
        return content[:5000]
