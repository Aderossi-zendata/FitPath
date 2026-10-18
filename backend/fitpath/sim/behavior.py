"""Shopper behaviour model of the twin, with the published evidence behind each effect.

The H&M data says what people buy, not how they move. Movement and in-store effects come from the
retail literature below. None of these numbers can be measured from the dataset, so each parameter
is a range: every simulation run draws its own value (seeded), and experiments with repetitions
report results that already include this uncertainty. Replace a range with a measured value when a
real store's data is available.
"""
import random
import statistics

from fitpath.sim import store_plan

SCHOLAR = "https://scholar.google.com/scholar?q="

SOURCES = {
    "hui2009": {"label": "Hui, Bradlow & Fader (2009), Journal of Consumer Research: crowding and purchases along the in-store path",
                "url": "https://sciencedaily.com/releases/2009/04/090420170911.htm"},
    "hui2009b": {"label": "Hui, Fader & Bradlow (2009): 'The Traveling Salesman Goes Shopping', real shopping paths are inefficient",
                 "url": "https://knowledge.wharton.upenn.edu/podcast/knowledge-at-wharton-podcast/the-traveling-salesman-goes-shopping-the-efficiency-of-purchasing-patterns-in-the-grocery-store/"},
    "hui2013": {"label": "Hui, Inman, Huang & Suher (2013), Journal of Marketing: the further shoppers walk, the more unplanned purchases",
                "url": "https://foxbusiness.com/features/to-increase-spending-make-customers-walk-farther"},
    "tan2018": {"label": "Tan, Corsi et al. (2018), Flinders/Ehrenberg-Bass: sales effectiveness of endcaps in different locations",
                "url": "https://researchnow.flinders.edu.au/en/publications/assessing-the-sales-effectiveness-of-differently-located-endcaps-/"},
    "ozgormus2015": {"label": "Ozgormus (2015), Auburn University: block layout with simulation, revenue and adjacencies from baskets",
                     "url": "https://etd.auburn.edu/handle/10415/4494"},
    "kesavan": {"label": "Kesavan et al., field experiment with an apparel retailer: fitting-room staff",
                "url": "https://strategy-business.com/article/00073"},
    "underhill": {"label": "Underhill, 'Why We Buy': decompression zone at the entrance, till area",
                  "url": SCHOLAR + "Underhill+Why+We+Buy+science+of+shopping"},
    "tuwien": {"label": "TU Wien: agent-based tool to evaluate store layouts",
               "url": "https://repositum.tuwien.at/handle/20.500.12708/9228"},
}

# position class of a block -> how much a department placed there attracts passers-by
ATTRACTION = {
    "sight_line": (1.3, 1.7), "window": (1.2, 1.5), "near_tills": (1.1, 1.4), "side_fitting": (1.0, 1.2),
    "side": (.9, 1.1), "center_back": (.8, 1.0), "side_back": (.7, .95), "back_wall": (.6, .9),
}

PARAMS = [
    {"key": "attraction", "name": "Location attraction",
     "what": "A department in sight from the entrance or in the window is visited and bought on impulse more often than one at the back of the store.",
     "range": ATTRACTION, "unit": "multiplier", "sources": ["tan2018", "underhill", "ozgormus2015"]},
    {"key": "pass_impulse", "name": "Purchases along the way",
     "what": "Walking past a department without stopping, there is a small chance of picking up an item: the further a shopper walks, the more unplanned purchases.",
     "range": (.006, .018), "unit": "probability per department passed", "sources": ["hui2013"]},
    {"key": "adjacency", "name": "Neighbouring departments bought together",
     "what": "When two departments that often end up on the same receipt (lift from H&M baskets) are close together, visitors of one more often pick something from the other.",
     "range": (.02, .06), "unit": "probability per unit of relative lift above average", "sources": ["ozgormus2015"]},
    {"key": "crowd", "name": "Crowding",
     "what": "Crowded departments attract people, but shoppers in a crowd buy less and stay less.",
     "range": (.10, .25), "unit": "reduction per extra person per 10 m² above the threshold", "sources": ["hui2009"]},
    {"key": "crowd_free", "name": "Crowding threshold",
     "what": "Density below which crowding has no effect.",
     "range": (.08, .12), "unit": "people per m²", "sources": ["hui2009"]},
    {"key": "attendant_conv", "name": "Fitting-room attendant",
     "what": "With a fitting-room attendant, shoppers are more likely to buy the items they tried on (10-store pilot: +2.1% conversion).",
     "range": (.01, .03), "unit": "relative increase", "sources": ["kesavan"]},
    {"key": "detour", "name": "Suboptimal paths",
     "what": "Shoppers do not take the shortest route between departments: part of the time they pick the next one at random, not the nearest.",
     "range": (.2, .4), "unit": "probability", "sources": ["hui2009b"]},
]
NOT_MODELLED = [
    {"what": "Tendency to turn right at the entrance: recent tracking studies do not confirm it, so it is not in the model.",
     "url": SCHOLAR + "Kim+2022+right+turn+bias+retail+store+shopper+path"},
    {"what": "Groups of shoppers entering together: for now every visitor is simulated alone.", "url": None},
]


class Behaviour:
    """Parameters of one run, drawn from the ranges above."""

    def __init__(self, seed: int):
        r = random.Random(f"behaviour-{seed}")
        self.attraction = {c: r.uniform(*rg) for c, rg in ATTRACTION.items()}
        for p in PARAMS:
            if p["key"] != "attraction":
                setattr(self, p["key"], r.uniform(*p["range"]))
        lifts = store_plan.zone_lift()
        self._lift_mid = statistics.median(lifts.values()) if lifts else 1.0

    def zone_attraction(self, layout: dict) -> dict:
        return {z: self.attraction[layout["blocks"][b]["cls"]] for z, b in layout["assign"].items()}

    def rel_lift(self, a: str, b: str) -> float:
        """Co-purchase lift of two departments relative to the typical pair (1 = average)."""
        return store_plan.lift_of(a, b) / self._lift_mid if "new_in" not in (a, b) else 1.0

    def neighbours(self, layout: dict) -> dict:
        """For each department, the departments in adjacent blocks with an above-average co-purchase lift."""
        inv = layout["assign"]
        out = {}
        for a, ba in inv.items():
            out[a] = [(b, self.rel_lift(a, b) - 1) for b, bb in inv.items()
                      if b != a and store_plan.adjacent(ba, bb) and self.rel_lift(a, b) > 1]
        return out

    def crowd_factor(self, people: int, area: float) -> float:
        extra = max(0.0, people / area - self.crowd_free) * 10
        return max(.4, 1 - self.crowd * extra)

    def values(self) -> dict:
        return {"attraction": {c: round(v, 2) for c, v in self.attraction.items()},
                **{p["key"]: round(getattr(self, p["key"]), 4) for p in PARAMS if p["key"] != "attraction"}}


def describe() -> dict:
    """For the UI and the agent: every assumption, its range and its sources."""
    return {"params": PARAMS, "sources": SOURCES, "not_modelled": NOT_MODELLED,
            "note": "Every simulation draws its own values from these ranges, so the repetitions of an experiment include the model's uncertainty."}
