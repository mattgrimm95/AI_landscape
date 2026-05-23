"""Curated story tours through the knowledge graph.

A learner faces the same question every newcomer to a domain faces: "where
should I look first?" Star-from-the-top is impossible — the graph is too big
to read at once — and a search box assumes you already know what you're
looking for. Tours bridge that gap: each one walks a reader through a small
ordered list of entities tied together by a single thread (the Iran war,
the AI laser stack, low-cost drone procurement, ...), with a one-sentence
narrative card per step. Clicking a step focuses the graph on the entity.

Tours are curated, in code, on purpose. They're load-bearing for *learning*,
which means the editorial choices (which threads exist, what order, what
the cards say) matter more than coverage; pulling them out into the corpus
would invite "drift" each scrape. Adding a tour is a deliberate act — drop
a new entry into `TOURS` below, point its `stops` at canonical entity names
the graph already has, and the UI picks it up.
"""

TOURS = [
    {
        "id": "iran_hormuz",
        "title": "Iran war & the Strait of Hormuz",
        "tagline": (
            "How a regional war shut a global chokepoint without firing a "
            "single mine."
        ),
        "stops": [
            {
                "entity": "Iran",
                "card": (
                    "The conflict that drives most 2026 reporting in the "
                    "corpus. Sets the stage for everything downstream."
                ),
            },
            {
                "entity": "Strait of Hormuz",
                "card": (
                    "Drone attacks plus repriced war-risk insurance — not a "
                    "blockade — collapsed tanker traffic by 80% in two days."
                ),
            },
            {
                "entity": "Operation Epic Fury",
                "card": (
                    "US military operation against Iran; AFSOC put AI-powered "
                    "ISR transfer into combat use from day one."
                ),
            },
            {
                "entity": "Brad Cooper",
                "card": (
                    "CENTCOM commander. Publicly disputed the assessment that "
                    "Iran still fields a viable missile arsenal."
                ),
            },
            {
                "entity": "Israel",
                "card": (
                    "Coalition partner; the IDF is building an FPV drone "
                    "factory in response to Hezbollah's southern Lebanon "
                    "operations."
                ),
            },
        ],
    },
    {
        "id": "laser_stack",
        "title": "The Pentagon's directed-energy stack",
        "tagline": (
            "Lasers move from labs to live-fire on carriers — and into the "
            "Golden Dome shield."
        ),
        "stops": [
            {
                "entity": "Joint Laser Weapon System",
                "card": (
                    "Army+Navy collaboration scaling a containerized "
                    "150–300 kW laser for cruise-missile defense under "
                    "Golden Dome."
                ),
            },
            {
                "entity": "Palletized High Energy Laser",
                "card": (
                    "20 kW P-HEL — based on AV's LOCUST — downed multiple "
                    "drones from the deck of the USS George H.W. Bush."
                ),
            },
            {
                "entity": "HELIOS laser",
                "card": (
                    "Navy 60 kW system already installed on the USS Preble; "
                    "the source of design lessons for JLWS."
                ),
            },
            {
                "entity": "IFPC-HEL",
                "card": (
                    "Army 300 kW Indirect-Fire Protection laser, first "
                    "prototype delivered for service evaluation."
                ),
            },
            {
                "entity": "Golden Dome",
                "card": (
                    "Trump's national missile-defense initiative — CBO put "
                    "the 20-year cost at $1.2 trillion."
                ),
            },
        ],
    },
    {
        "id": "low_cost_strike",
        "title": "Low-cost strike: the new defense vendor cohort",
        "tagline": (
            "Five upstarts and a $10K-per-shot mantra are reshaping the "
            "Pentagon's procurement of munitions and drones."
        ),
        "stops": [
            {
                "entity": "Low-Cost Containerized Munitions",
                "card": (
                    "LCCM program — 10,000 cheap cruise missiles in three "
                    "years across four firm-fixed-price vendors."
                ),
            },
            {
                "entity": "Anduril",
                "card": (
                    "$5B funding round in May 2026; one of four LCCM "
                    "vendors. The incumbent challenger of the cohort."
                ),
            },
            {
                "entity": "Castelion",
                "card": (
                    "Startup making the Blackbeard hypersonic strike weapon; "
                    "parallel framework to LCCM for hypersonic."
                ),
            },
            {
                "entity": "Perennial Autonomy",
                "card": (
                    "Maker of the $15K Merops interceptor; bought by "
                    "Lithuania and used by US forces to down Iranian Shaheds."
                ),
            },
            {
                "entity": "Shahed",
                "card": (
                    "The Iranian one-way-attack drone the cohort is built "
                    "to defeat — refined by Russia in Ukraine, then "
                    "re-exported."
                ),
            },
        ],
    },
    {
        "id": "frontier_models",
        "title": "Frontier-model competition",
        "tagline": (
            "Where the major AI labs sat in May 2026 — and how their releases "
            "shape the rest of the landscape."
        ),
        "stops": [
            {
                "entity": "OpenAI",
                "card": (
                    "GPT-5.5 in production at NVIDIA, Uber, Sea Limited; "
                    "Codex on AWS; Cybersecurity Action Plan + TAC."
                ),
            },
            {
                "entity": "Google DeepMind",
                "card": (
                    "Gemini 3.x family + Antigravity agent platform; "
                    "AlphaEvolve recursively optimizing TPU silicon."
                ),
            },
            {
                "entity": "Anthropic",
                "card": (
                    "Claude. Powers the optional analyst-narrative "
                    "synthesis in this app."
                ),
            },
            {
                "entity": "NVIDIA",
                "card": (
                    "40,000 engineers using Codex; GB200/GB300 are now the "
                    "default substrate for frontier training."
                ),
            },
            {
                "entity": "Stargate",
                "card": (
                    "OpenAI's compute build-out — 10 GW in 18 months. The "
                    "scale flywheel the labs are racing on."
                ),
            },
        ],
    },
    {
        "id": "replicator_cca",
        "title": "Replicator → CCA: autonomy in US doctrine",
        "tagline": (
            "How three years of DoD initiatives produced the next-gen "
            "unmanned fighter program."
        ),
        "stops": [
            {
                "entity": "Replicator Initiative",
                "card": (
                    "The 2023 mass-production push for attritable autonomy. "
                    "The cohort below grew out of its lessons (and its "
                    "stumbles)."
                ),
            },
            {
                "entity": "Collaborative Combat Aircraft",
                "card": (
                    "CCA — the Air Force's autonomous wingman program; "
                    "$1.4B requested for FY27 development."
                ),
            },
            {
                "entity": "YFQ-42A",
                "card": (
                    "General Atomics' CCA prototype, alongside Anduril's "
                    "YFQ-44A. Picked for Increment 1."
                ),
            },
            {
                "entity": "MQ-9 Reaper",
                "card": (
                    "The incumbent the Air Force is now writing a "
                    "cheap-and-attritable replacement spec to displace."
                ),
            },
            {
                "entity": "Drone Wingman",
                "card": (
                    "The doctrinal concept the CCA program is built to "
                    "execute — autonomy paired with manned fighters."
                ),
            },
        ],
    },
]


def build_tour_index():
    """Return the curated tours as a plain JSON-safe list for the API."""
    return [
        {
            "id": tour["id"],
            "title": tour["title"],
            "tagline": tour["tagline"],
            "stops": list(tour["stops"]),
        }
        for tour in TOURS
    ]


def get_tour(tour_id):
    """Return one tour by id, or None."""
    for tour in TOURS:
        if tour["id"] == tour_id:
            return {
                "id": tour["id"],
                "title": tour["title"],
                "tagline": tour["tagline"],
                "stops": list(tour["stops"]),
            }
    return None
