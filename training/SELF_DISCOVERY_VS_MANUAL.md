# Head-to-Head Comparison: Human-Guided Rubric (`v3`) vs. Zero-Prior AI Self-Discovered Rubric

## 1. Experimental Setup

To test whether the AI can learn **what a hero should do at specific moments and states** without human domain guidance:
* **Input (`data/universal_sao_dataset.json`)**: All 41 teamfights from our 15 real `owreplays.tv` matches were converted into hero-agnostic `(State, Action, Outcome)` tuples:
  - `State`: `(num_state [5v5 / 5v4 / 4v5], hp_state [>=120 / <120], seq_pos [first_action_in_fight / mid_or_late_fight_action], dive_threat [True / False])`
  - `Action`: `(nade:offensive_anti, nade:self_heal, nade:defensive_heal, sleep:long_range_poke, sleep:close_or_mid_target, ult_on:Target)`
  - `Outcome`: `(fight_won [0/1], player_died_in_fight [0/1])`
* **Zero Prior Constraints**: `training/auto_discover_rubric.py` did **not** load `rubrics/ana_diamond.json` and received zero coaching hints in its prompt.

---

## 2. Key Differences: Where AI Self-Discovery Outperformed Our Manual Analysis

| Dimension | Manual / Human-Guided Rubric (`ana_diamond.json`) | Zero-Prior AI Self-Discovered Rubric (`ana_auto_discovered.json`) | Why AI Self-Discovery Won |
| :--- | :--- | :--- | :--- |
| **1. Self-Heal Biotic Grenade (`nade:self_heal`)** | **Blanket Penalty**: Flagged `self_heal_nade_ratio >= 30%` as a bad Diamond habit across the board, falsely penalizing won fights in `D86HQF` and `HZJK6E`. | **State-Conditioned Inversion (`TL-04` / `RULE-ANA-03`)**: Discovered that when `state = (4v5 numbers_disadvantage, critical_hp < 120, dive_threat=True)`, `nade:self_heal` has a **100% fight win rate and 0% death rate** (`CP9GCK`, `D86HQF`, `HZJK6E`, `NX415N`)! | Automatically solved the "team/survival context" problem without human prompting by conditioning on `(4v5, HP < 120)`. |
| **2. Defining "Early / Bad Offensive Nade" (`nade:offensive_anti`)** | **Circular Definition**: Defined bad offensive nades by whether the fight was eventually lost (`unconverted_poke_nade`). | **Predictive Sequence Timing (`TL-02` vs `TL-03` / `RULE-ANA-02`)**: Discovered that `nade:offensive_anti` as the **`first_action_in_fight`** (5v5 pre-engage) has only a **20% win rate (40% death rate)**, whereas delaying `nade:offensive_anti` to **`mid_or_late_fight_action`** (after initial engage/stabilization) has a **100% win rate (0% death rate) in GM**! | Replaced outcome-circular logic with a true temporal rule: **never open a 5v5 fight with E as your first action; wait until mid-fight after tanks bind**. |
| **3. Enemy Composition Adaptation (`Dive` vs `Poke`)** | **Static Rule**: Applied the same Sleep Dart rule regardless of whether the enemy ran Poke or Dive. | **Contextual Interaction Law #1**: Discovered that against Dive (`Tracer/Genji/Doomfist/Sombra`), `sleep:long_range_poke` yields **0% fight win rate (50% death rate)**, while `sleep:close_or_mid_target (<15m)` yields **83.3% win rate in Diamond / 66.7% in GM**. Against non-dive Poke (`Roadhog/Orisa`), proactive mid-range sleeps on frontline convert cleanly. | Automatically separated anti-dive discipline from anti-poke frontline setups based on `dive_threat`. |
| **4. Ultimate Timing (`Nano Boost`)** | **Negative Rule Only**: Only checked if Nano was wasted in a `3v5` loss. | **Positive Timing Discovery (`TL-05` / `RULE-ANA-04`)**: Discovered that deploying Nano Boost on the Tank (`Mauga`, `Winston`, `D.Va`) in **`5v4 numbers_advantage`** (`mid_or_late_fight_action`) achieved a **100% fight win rate** across both Diamond and GM (`D86HQF`, `CP9GCK`, `MFTXPE`). | Tells the player *when to press Q* (`5v4` snowball), not just when not to press Q. |
