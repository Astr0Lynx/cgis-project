"""CyberGIS Level 1: India-wide dasymetric redistribution of WorldPop counts by GHSL residential surface.

Implements the original flowchart (page 1):

    R_e   = max(GHS-BUILT-S total_e - GHS-BUILT-S NRES_e, 0)     for GHSL epochs e = 2000, 2005, 2010
    R_t   = temporal interpolation of R_e to target year t       (2011 policy: see METHODOLOGY_DECISION.md)
    p_ai  = P_a * R_ai / sum_j R_aj                              (P_a: WorldPop count of parent a)

See ``science/level1/METHOD.md``.
"""

__version__ = "1.0.0"
