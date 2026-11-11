"""Background workers. ``ingest`` runs the existing importer (assets read-write, cgis_ingest DB privileges);
``export`` writes conversions/clips to the export volume (assets read-only). Neither talks to Docker."""
