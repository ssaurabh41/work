import json
import os
import sys
from datetime import datetime

DATA_FILE = "expenses.json"


def load_expenses():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE) as f:
        return json.load(f)


def save_expenses(expenses):
    with open(DATA_FILE, "w") as f:
        json.dump(expenses, f, indent=2)


def add_expense(amount, category, description):
    expenses = load_expenses()
    expense = {
        "id": int(datetime.now().timestamp() * 1000),
        "amount": float(amount),
        "category": category,
        "description": description,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }
    expenses.append(expense)
    save_expenses(expenses)
    print(f"Added: {description} — ${float(amount):.2f} [{category}]")


def list_expenses(category=None):
    expenses = load_expenses()
    if category:
        expenses = [e for e in expenses if e["category"].lower() == category.lower()]
    if not expenses:
        print("No expenses found.")
        return
    print(f"\n{'ID':<15} {'Date':<12} {'Category':<15} {'Amount':>8}  Description")
    print("-" * 65)
    for e in expenses:
        print(f"{e['id']:<15} {e['date']:<12} {e['category']:<15} ${e['amount']:>7.2f}  {e['description']}")
    total = sum(e["amount"] for e in expenses)
    print("-" * 65)
    print(f"{'Total':<43} ${total:>7.2f}")


def summary():
    expenses = load_expenses()
    if not expenses:
        print("No expenses found.")
        return
    totals = {}
    for e in expenses:
        totals[e["category"]] = totals.get(e["category"], 0) + e["amount"]
    print("\nExpense Summary by Category:")
    print("-" * 30)
    for cat, total in sorted(totals.items(), key=lambda x: -x[1]):
        print(f"  {cat:<20} ${total:.2f}")
    print("-" * 30)
    print(f"  {'Grand Total':<20} ${sum(totals.values()):.2f}")


def delete_expense(expense_id):
    expenses = load_expenses()
    before = len(expenses)
    expenses = [e for e in expenses if e["id"] != int(expense_id)]
    if len(expenses) == before:
        print(f"No expense found with id {expense_id}.")
        return
    save_expenses(expenses)
    print(f"Deleted expense {expense_id}.")


def print_help():
    print("""
Expense Tracker — Usage:
  python expense_tracker.py add <amount> <category> <description>
  python expense_tracker.py list [category]
  python expense_tracker.py summary
  python expense_tracker.py delete <id>
  python expense_tracker.py help

Examples:
  python expense_tracker.py add 12.50 Food "Lunch at cafe"
  python expense_tracker.py add 45.00 Transport "Monthly bus pass"
  python expense_tracker.py list
  python expense_tracker.py list Food
  python expense_tracker.py summary
""")


def main():
    args = sys.argv[1:]
    if not args or args[0] == "help":
        print_help()
    elif args[0] == "add" and len(args) >= 4:
        add_expense(args[1], args[2], " ".join(args[3:]))
    elif args[0] == "list":
        list_expenses(args[1] if len(args) > 1 else None)
    elif args[0] == "summary":
        summary()
    elif args[0] == "delete" and len(args) == 2:
        delete_expense(args[1])
    else:
        print("Invalid command. Run `python expense_tracker.py help` for usage.")


if __name__ == "__main__":
    main()
