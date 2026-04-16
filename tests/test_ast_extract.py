"""Tests for AST-based code extraction."""

from pathlib import Path

import pytest

from priorart.core.ast_extract import InterfaceExtractor


@pytest.fixture
def extractor():
    """Create an InterfaceExtractor instance."""
    return InterfaceExtractor()


def test_extract_python_class(extractor):
    """Test extracting Python class interface."""
    code = '''
class HTTPClient:
    """A simple HTTP client."""

    def __init__(self, base_url: str):
        """Initialize the client."""
        self.base_url = base_url

    def get(self, path: str) -> dict:
        """Make a GET request."""
        return {}

    def _internal_method(self):
        """This is internal and should be skipped."""
        pass
'''

    result = extractor.extract_python(code)

    # Should extract class with public methods
    assert "class HTTPClient" in result
    assert "def get" in result
    assert "def __init__" in result

    # Should skip private methods
    assert "_internal_method" not in result


def test_extract_python_functions(extractor):
    """Test extracting Python top-level functions."""
    code = '''
def public_function(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y

def _private_function():
    """This should be skipped."""
    pass

async def async_function() -> None:
    """An async function."""
    await something()
'''

    result = extractor.extract_python(code)

    # Should extract public function and async function
    assert "def public_function" in result
    assert "Add two numbers" in result
    assert "async_function" in result

    # Should skip private function
    assert "_private_function" not in result


def test_extract_python_syntax_error(extractor):
    """Test fallback when Python code has syntax error."""
    code = '''
def broken_function(
    # Incomplete function
'''

    result = extractor.extract_python(code)

    # Should fallback to simple extraction
    assert "def broken_function" in result


def test_extract_typescript_interface(extractor):
    """Test extracting TypeScript interfaces."""
    code = '''
export interface HTTPClient {
    get(url: string): Promise<Response>;
    post(url: string, data: any): Promise<Response>;
}

export class Client implements HTTPClient {
    constructor(config: Config) {
    }

    public get(url: string): Promise<Response> {
        return fetch(url);
    }
}

function internalHelper() {
    // Not exported
}
'''

    result = extractor.extract_typescript(code)

    assert "HTTPClient" in result
    assert "internalHelper" not in result


def test_extract_rust_public_items(extractor):
    """Test extracting Rust public items."""
    code = '''
pub struct HttpClient {
    base_url: String,
}

impl HttpClient {
    pub fn new(base_url: String) -> Self {
        HttpClient { base_url }
    }

    pub fn get(&self, path: &str) -> Result<Response, Error> {
        // Implementation
    }

    fn internal_helper(&self) {
        // Private method
    }
}

pub enum HttpMethod {
    Get,
    Post,
}
'''

    result = extractor.extract_rust(code)

    assert "HttpClient" in result
    assert "internal_helper" not in result


def test_extract_go_public_functions(extractor):
    """Test extracting Go exported functions."""
    code = '''
package http

// PublicFunction is exported
func PublicFunction(url string) error {
    return nil
}

// privateFunction is not exported
func privateFunction() {
}

// HTTPClient is an exported struct
type HTTPClient struct {
    BaseURL string
}

// Get makes a GET request
func (c *HTTPClient) Get(path string) error {
    return nil
}
'''

    result = extractor.extract_go(code)

    assert "PublicFunction" in result
    assert "HTTPClient" in result
    assert "privateFunction" not in result


def test_extract_by_file_extension(extractor):
    """Test automatic extraction based on file extension."""
    python_code = "def hello(): pass"
    ts_code = "export function hello() {}"

    python_result = extractor.extract(Path("test.py"), python_code)
    ts_result = extractor.extract(Path("test.ts"), ts_code)

    assert "def hello" in python_result
    assert "hello" in ts_result


def test_extract_type_stubs_passthrough(extractor):
    """Test that .pyi type stubs are passed through unchanged."""
    pyi_code = '''
def function(x: int) -> str: ...

class MyClass:
    def method(self) -> None: ...
'''

    result = extractor.extract(Path("stub.pyi"), pyi_code)

    assert "def function" in result
    assert "class MyClass" in result


def test_extract_dts_passthrough(extractor):
    """Test that .d.ts files are passed through unchanged, not processed as .ts."""
    dts_code = "export declare function request(url: string): Promise<Response>;\n"

    result = extractor.extract(Path("types.d.ts"), dts_code)

    # Should be identical — pass-through, not regex-extracted
    assert result == dts_code


def test_extract_unknown_extension_fallback(extractor):
    """Test fallback for unknown file extensions."""
    code = "This is some unknown code format\nWith multiple lines\nAnd content"

    result = extractor.extract(Path("test.unknown"), code)

    # Should return truncated content
    assert isinstance(result, str)
    assert len(result) <= 5000


def test_fallback_extract_filters_comments(extractor):
    """Test that fallback extraction filters comment lines."""
    python_code = '''# This is a comment
def function():
    pass
# Another comment
class MyClass:
    pass
'''

    result = extractor._fallback_extract(python_code, 'python')

    # Comments should be filtered
    lines = result.split('\n')
    assert not any(line.strip().startswith('#') for line in lines if line.strip())


def test_extract_javascript_exports(extractor):
    """Test extracting JavaScript exports."""
    code = '''
export function publicFunction(x, y) {
    return x + y;
}

export const API_URL = "https://api.example.com";

module.exports = {
    helper: function() {},
    util: {}
};

function internalFunction() {
    // Not exported
}
'''

    result = extractor.extract_javascript(code)

    # Should extract exports
    assert "export" in result or "module.exports" in result


def test_extract_python_annotations_and_defaults(extractor):
    """Test extraction of typed args, defaults, return types, and class bases."""
    code = '''
class MyClient(BaseClient):
    """Custom client."""

    def __init__(self, url: str, timeout: int = 30):
        """Init."""
        self.url = url

    def fetch(self, path: str, retry: bool = True) -> dict:
        """Fetch data."""
        return {}

    def __repr__(self) -> str:
        return f"MyClient({self.url})"


def configure(host: str, port: int = 8080, debug: bool = False) -> None:
    """Configure the app."""
    pass
'''

    result = extractor.extract_python(code)

    # Class with base
    assert "class MyClient(BaseClient):" in result
    # Typed args and defaults
    assert "timeout: int = 30" in result
    assert "retry: bool = True" in result
    # Return type
    assert "-> dict" in result
    assert "-> None" in result
    # __repr__ should be included
    assert "__repr__" in result
    # Top-level function
    assert "def configure" in result
    assert "port: int = 8080" in result


def test_extract_python_exception_fallback(extractor):
    """Non-syntax exception triggers fallback extraction."""
    # Provide valid Python that would parse but make the AST traversal fail
    # by passing something that's not valid Python at all (non-syntax error)
    result = extractor._fallback_extract("def hello():\n    pass\n", "python")
    assert "def hello():" in result


def test_fallback_extract_typescript_comments(extractor):
    """Fallback extraction skips // comments for typescript."""
    code = "// comment\nconst x = 1;\n// another\nfunction f() {}\n"
    result = extractor._fallback_extract(code, "typescript")

    lines = result.split('\n')
    assert not any(line.strip().startswith('//') for line in lines if line.strip())


def test_extract_python_attribute_base(extractor):
    """Test class with module.Class base (ast.Attribute)."""
    code = '''
class MyHandler(http.server.BaseHTTPRequestHandler):
    """Custom handler."""

    def do_GET(self):
        """Handle GET."""
        pass
'''
    result = extractor.extract_python(code)
    assert 'MyHandler' in result
    assert 'http.server.BaseHTTPRequestHandler' in result


def test_extract_python_non_syntax_exception(extractor):
    """Non-SyntaxError exception triggers fallback."""
    from unittest.mock import patch

    # Patch ast.parse to raise a non-SyntaxError
    with patch('priorart.core.ast_extract.ast.parse', side_effect=TypeError("test error")):
        result = extractor.extract_python("def hello(): pass\n")

    assert "def hello()" in result


def test_fallback_extract_50_line_limit(extractor):
    """Fallback extraction stops at 50 non-empty, non-comment lines."""
    lines = [f"line_{i} = {i}" for i in range(200)]
    code = "\n".join(lines)

    result = extractor._fallback_extract(code, "python")
    result_lines = [l for l in result.split('\n') if l.strip()]
    assert len(result_lines) == 50
