"""Domain layer: entities, value objects and domain services.

Pure Python. No I/O, no framework or provider imports, no Garmin-specific types (ADR-002).
Every non-reference aggregate carries ``athlete_id`` as its ownership root (ADR-016).
"""
