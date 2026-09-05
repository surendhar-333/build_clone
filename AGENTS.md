# Agent Instructions for Databricks Payment Settlement & Reconciliation

## Environment and Tooling
- **Python Version**: Python 3.11
- **Code Formatting and Linting**: Use `ruff` and `black` configured for a line length of 100.
- **Testing**: Tests are located in the `tests/` directory. Run them using the command: `PYTHONPATH=. pytest`

## Project Structure and File Guidelines
- **`notebooks/`**: These files contain Spark code. They **CANNOT execute in this VM**. Therefore, you should only perform syntax checks (`py_compile`) and linting on these files. Do not try to run them.
- **`docs/DATA_MODEL.md`**: This file represents the **frozen schema contract** for the lakehouse. It is strictly editable *only* by this initial task. Future tasks should reference this file but not modify it.
