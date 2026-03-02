"""
AST-based code extraction for repository ingestion.

Extracts public interfaces from source files to fit within character budget.
"""

import ast
import re
import logging
from typing import List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


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
            tree = ast.parse(content)
            lines = []

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # Extract class definition
                    lines.append(self._extract_class(node))

                elif isinstance(node, ast.FunctionDef):
                    # Skip private functions
                    if not node.name.startswith('_'):
                        lines.append(self._extract_function(node))

            return '\n\n'.join(lines)

        except SyntaxError as e:
            logger.warning(f"Python syntax error: {e}")
            return self._fallback_extract(content, 'python')
        except Exception as e:
            logger.warning(f"Python extraction error: {e}")
            return self._fallback_extract(content, 'python')

    def _extract_class(self, node: ast.ClassDef) -> str:
        """Extract class definition with methods."""
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
            if isinstance(item, ast.FunctionDef):
                # Skip private methods
                if not item.name.startswith('_') or item.name in ['__init__', '__str__', '__repr__']:
                    method_str = self._extract_method(item)
                    lines.append(method_str)

        return '\n'.join(lines)

    def _extract_function(self, node: ast.FunctionDef) -> str:
        """Extract function signature and docstring."""
        # Get signature
        args = []
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                try:
                    arg_str += f": {ast.unparse(arg.annotation)}"
                except:
                    pass
            args.append(arg_str)

        # Add defaults
        defaults = node.args.defaults
        if defaults:
            for i, default in enumerate(defaults, len(args) - len(defaults)):
                try:
                    args[i] += f" = {ast.unparse(default)}"
                except:
                    args[i] += " = ..."

        # Return type annotation
        returns = ""
        if node.returns:
            try:
                returns = f" -> {ast.unparse(node.returns)}"
            except:
                pass

        signature = f"def {node.name}({', '.join(args)}){returns}:"

        # Get docstring
        docstring = ast.get_docstring(node)
        if docstring:
            return f"{signature}\n    \"\"\"{docstring}\"\"\"\n    ..."
        else:
            return f"{signature}\n    ..."

    def _extract_method(self, node: ast.FunctionDef) -> str:
        """Extract method signature (indented for class)."""
        func_str = self._extract_function(node)
        # Indent each line
        lines = func_str.split('\n')
        return '\n'.join('    ' + line if line else '' for line in lines)

    def extract_typescript(self, content: str) -> str:
        """Extract TypeScript public interface using regex."""
        patterns = [
            # Export functions
            r'export\s+(?:async\s+)?function\s+\w+\s*\([^)]*\)[^{]*',
            # Export classes
            r'export\s+class\s+\w+(?:\s+extends\s+\w+)?(?:\s+implements\s+[\w\s,]+)?\s*\{[^}]*(?:constructor|public)[^}]*\}',
            # Export interfaces
            r'export\s+interface\s+\w+\s*\{[^}]*\}',
            # Export types
            r'export\s+type\s+\w+\s*=[^;]+;',
            # Export const
            r'export\s+const\s+\w+\s*:\s*[^=]+=',
        ]

        extracted = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
            extracted.extend(matches[:10])  # Limit matches per pattern

        return '\n\n'.join(extracted) if extracted else self._fallback_extract(content, 'typescript')

    def extract_javascript(self, content: str) -> str:
        """Extract JavaScript public interface using regex."""
        patterns = [
            # Export functions (ES6)
            r'export\s+(?:async\s+)?function\s+\w+\s*\([^)]*\)[^{]*',
            # Export arrow functions
            r'export\s+const\s+\w+\s*=\s*(?:async\s*)?\([^)]*\)\s*=>[^;]+;',
            # Export classes
            r'export\s+class\s+\w+(?:\s+extends\s+\w+)?\s*\{[^}]*constructor[^}]*\}',
            # Module exports
            r'module\.exports\s*=\s*\{[^}]+\}',
            # Named exports
            r'exports\.\w+\s*=\s*(?:function|class|\{)[^;]+;',
        ]

        extracted = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
            extracted.extend(matches[:10])

        return '\n\n'.join(extracted) if extracted else self._fallback_extract(content, 'javascript')

    def extract_rust(self, content: str) -> str:
        """Extract Rust public interface using regex."""
        patterns = [
            # Public functions
            r'pub\s+(?:async\s+)?fn\s+\w+[^{]*\{',
            # Public structs
            r'pub\s+struct\s+\w+(?:<[^>]+>)?\s*(?:\{[^}]*\}|\([^)]*\)|;)',
            # Public enums
            r'pub\s+enum\s+\w+(?:<[^>]+>)?\s*\{[^}]*\}',
            # Public traits
            r'pub\s+trait\s+\w+(?:<[^>]+>)?\s*\{[^}]*\}',
            # Public type aliases
            r'pub\s+type\s+\w+\s*=\s*[^;]+;',
            # Public constants
            r'pub\s+const\s+\w+:\s*[^=]+=\s*[^;]+;',
        ]

        extracted = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
            # Clean up matches (remove body, keep signature)
            cleaned = []
            for match in matches[:10]:
                # For functions, remove body
                if 'fn ' in match:
                    match = match.split('{')[0] + '{ ... }'
                cleaned.append(match)
            extracted.extend(cleaned)

        return '\n\n'.join(extracted) if extracted else self._fallback_extract(content, 'rust')

    def extract_go(self, content: str) -> str:
        """Extract Go public interface using regex."""
        patterns = [
            # Public functions (capitalized)
            r'^func\s+[A-Z]\w*\s*\([^)]*\)[^{]*',
            # Public methods
            r'^func\s+\([^)]+\)\s+[A-Z]\w*\s*\([^)]*\)[^{]*',
            # Public types
            r'^type\s+[A-Z]\w*\s+(?:struct|interface)\s*\{[^}]*\}',
            # Public constants
            r'^const\s+[A-Z]\w*\s+[^=]+=',
            # Public variables
            r'^var\s+[A-Z]\w*\s+[^=]+=',
        ]

        extracted = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            extracted.extend(matches[:10])

        return '\n\n'.join(extracted) if extracted else self._fallback_extract(content, 'go')

    def _fallback_extract(self, content: str, language: str) -> str:
        """Fallback extraction when AST/regex fails."""
        lines = content.split('\n')

        # Take first 50 non-empty, non-comment lines
        extracted = []
        for line in lines[:200]:
            # Skip empty lines
            if not line.strip():
                continue

            # Skip pure comment lines
            if language in ['python'] and line.strip().startswith('#'):
                continue
            if language in ['javascript', 'typescript', 'rust', 'go'] and line.strip().startswith('//'):
                continue

            extracted.append(line)

            if len(extracted) >= 50:
                break

        return '\n'.join(extracted)

    def extract(self, file_path: Path, content: str) -> str:
        """Extract interface based on file extension.

        Args:
            file_path: Path to file (for extension detection)
            content: File content

        Returns:
            Extracted interface or truncated content
        """
        extension = file_path.suffix.lower()

        extractors = {
            '.py': self.extract_python,
            '.pyi': lambda c: c,  # Type stubs are already interface-only
            '.ts': self.extract_typescript,
            '.tsx': self.extract_typescript,
            '.d.ts': lambda c: c,  # Type definitions are already interface-only
            '.js': self.extract_javascript,
            '.jsx': self.extract_javascript,
            '.rs': self.extract_rust,
            '.go': self.extract_go,
        }

        extractor = extractors.get(extension)
        if extractor:
            return extractor(content)

        # Unknown type - return first part
        return content[:5000]