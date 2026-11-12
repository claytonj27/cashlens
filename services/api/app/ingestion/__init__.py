"""Source adapters publish provider data through one canonical contract.

`contract` defines the canonical records, `synthetic` reads the fixture
provider and converts its sign convention, and `sync` stages update pages as
raw events and applies them atomically through the database function
`apply_sync_batch`, committing the provider cursor only on success.
"""
