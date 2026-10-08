# Tools

Your agent has one way to act: the `bash` tool that mini-swe-agent gives the model. Every tool
you build is a command line program the agent runs through bash. That is the only way to give the
agent new abilities. MCP servers and extra function-calling tools are not supported.

## Folders

- `blackbox/`: tools available in black-box mode
- `whitebox/`: tools available in white-box mode
- `combined/`: tools available in combined mode, which also sees the black-box and white-box tools
- `shared/`: tools available in every mode

If two visible folders contain a tool with the same name, the mode's own folder wins.

## Rules for a tool

1. It is an executable file (`chmod +x`) with a shebang line, for example `#!/usr/bin/env python3`.
2. `toolname --help` prints a short description and usage. The harness puts this text into the
   prompt automatically, so this is how the agent learns about your tool.
3. It runs inside the task folder. It may read `SPEC.md`, `solution.py` and `tests/`.
4. It must not call any LLM or any network service. All model calls go through the agent.
5. It may only use the Python standard library and the packages in `pyproject.toml`.
6. It must finish within the command timeout in `settings.yaml` and print compact output.
7. In black-box mode it must not try to get at the real implementation.

## Examples included

- `shared/check_tests`: checks syntax, collection and missing assertions without running tests.
- `blackbox/list_constraints`: lists numeric constraints from the spec with boundary values.
- `whitebox/run_tests`: runs the tests against `solution.py`.
- `whitebox/coverage_report`: shows missed lines and branches.
