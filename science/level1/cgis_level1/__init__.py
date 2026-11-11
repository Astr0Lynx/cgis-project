# CyberGIS Level 1: India-wide dasymetric redistribution of WorldPop counts by GHSL residential surface.
#   R_e  = max(GHS-BUILT-S total_e - GHS-BUILT-S NRES_e, 0)   for GHSL epochs e = 2000, 2005, 2010 (+2015 for 2011)
#   R_t  = linear interpolation of R_e to target year t (2011: configured temporal.policy_2011)
#   p_ai = P_a * R_ai / sum_j R_aj                            (P_a: WorldPop count of 10x10-cell parent a)

__version__ = "1.0.0"
