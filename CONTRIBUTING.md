# Best Practices

## Coding

- Pycharm is recommended.
- uv is preferred over ruff.
- Always use type annotations
- Use pathlib instead of open()
- Catch the exact Exception type like ValueError instead of the generic "Exception" when possible
- Use async code

## Merging

- Use ruff for linting and formatting. Use ty for checking types.
  uvx ruff format
  uvx ruff check --fix
  uvx ty check

- Always test your changes in the development environment before making a pull request.
- Use squash to merge pull requests.

### Best Practices

- Use typing.ClassVar for class variables. For example: `ClassVar[list[str]]` not `list[str]`.
- Use pathlib for reads and writes. For example `pathlib.Path(file).write_text(json.dumps(data))` or `data = json.loads(pathlib.Path(file).read_text())`
- Use `pathlib.Path(file).is_file()` not `os.path.isfile(...)`
