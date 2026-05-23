"""
Per-table repository modules. Each module exposes free functions that take a
sqlite3.Connection as the first arg. ``WorldDatabase`` delegates its public
methods straight through to keep existing callers working.

The split exists so adding a new method for, say, NPC memory doesn't require
opening a 1000+ line god-class file — and so each table's queries can be read
in isolation.
"""
