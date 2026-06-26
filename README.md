# Expense Tracker CLI

A simple command-line expense tracker written in Python. No external dependencies — just the standard library.

## Usage

```bash
# Add an expense
python expense_tracker.py add <amount> <category> <description>

# List all expenses
python expense_tracker.py list

# List by category
python expense_tracker.py list Food

# Show summary by category
python expense_tracker.py summary

# Delete an expense by ID
python expense_tracker.py delete <id>
```

## Examples

```bash
python expense_tracker.py add 12.50 Food "Lunch at cafe"
python expense_tracker.py add 45.00 Transport "Monthly bus pass"
python expense_tracker.py add 9.99 Entertainment "Netflix"
python expense_tracker.py list
python expense_tracker.py summary
```

## Run Tests

```bash
python -m pytest test_expense_tracker.py -v
# or
python test_expense_tracker.py
```

## Data Storage

Expenses are saved locally in `expenses.json` in the project directory.
