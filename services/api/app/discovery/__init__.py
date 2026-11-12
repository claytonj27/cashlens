"""The Discovery portal: an internal customer-specific workspace inside CashLens.

Named for a customer only behind authentication: see ADR 0014 in docs/decisions. The design is
docs/discovery-architecture.md and its two companions.

Phase 1 ships the foundation only: roles read from `public.profiles`, the authorization
dependencies every later route composes, and the one admin route the interface needs to know
whether to draw a link. Nothing here is user-visible on its own.
"""
