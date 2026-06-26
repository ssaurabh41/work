import json
import os
import unittest
from unittest.mock import patch

# Use a temp data file during tests
import expense_tracker
expense_tracker.DATA_FILE = "test_expenses.json"


class TestExpenseTracker(unittest.TestCase):
    def setUp(self):
        if os.path.exists("test_expenses.json"):
            os.remove("test_expenses.json")

    def tearDown(self):
        if os.path.exists("test_expenses.json"):
            os.remove("test_expenses.json")

    def test_add_and_load(self):
        expense_tracker.add_expense(10.0, "Food", "Coffee")
        expenses = expense_tracker.load_expenses()
        self.assertEqual(len(expenses), 1)
        self.assertEqual(expenses[0]["amount"], 10.0)
        self.assertEqual(expenses[0]["category"], "Food")
        self.assertEqual(expenses[0]["description"], "Coffee")

    def test_delete(self):
        expense_tracker.add_expense(5.0, "Food", "Snack")
        expenses = expense_tracker.load_expenses()
        expense_id = expenses[0]["id"]
        expense_tracker.delete_expense(expense_id)
        self.assertEqual(len(expense_tracker.load_expenses()), 0)

    def test_summary(self):
        expense_tracker.add_expense(20.0, "Food", "Dinner")
        expense_tracker.add_expense(15.0, "Food", "Lunch")
        expense_tracker.add_expense(50.0, "Transport", "Train")
        expenses = expense_tracker.load_expenses()
        totals = {}
        for e in expenses:
            totals[e["category"]] = totals.get(e["category"], 0) + e["amount"]
        self.assertAlmostEqual(totals["Food"], 35.0)
        self.assertAlmostEqual(totals["Transport"], 50.0)

    def test_empty_list(self):
        expenses = expense_tracker.load_expenses()
        self.assertEqual(expenses, [])


if __name__ == "__main__":
    unittest.main()
