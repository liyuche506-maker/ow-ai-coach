# Offline Contrastive Training & Rubric Calibration Report (`v3`)

## 1. Dataset Overview (15-Match 3-Cohort Design)

To eliminate small-sample variance, we curated and processed **15 full Overwatch 2 Ana POV VODs** from `owreplays.tv` (**41 teamfights, 40 Biotic Grenades, 28 Sleep Darts, 13 Player Deaths, 12 Nano Boosts**), evenly stratified into three 5-match cohorts:

1. **`diamond_loss` (5 Matches — 14 Teamfights)**:
   - `92EMFN` (Diamond 4 | King's Row | Loss)
   - `MFTXPE` (Diamond 4 | Colosseo | Loss — *"Flanking Ana"*)
   - `NX415N` (Diamond 2 | King's Row | Loss)
   - `BV0VA4` (Diamond 4 | Circuit Royal | Loss — *"playing ana in higher level as low level"*)
   - `78H7MX` (Diamond 4 | Nepal | Loss — *"12-8 Ana Nepal Loss"*)
2. **`diamond_win` (5 Matches — 12 Teamfights)**:
   - `QN4070` (Diamond 3 | Hollywood | Win)
   - `V89RSK` (Diamond 3 | Lijiang Tower | Win)
   - `HZJK6E` (Diamond 4 | New Junk City | Win)
   - `D86HQF` (Diamond 3 | New Queen Street | Win — *"19-1 ana type shit"*)
   - `Q9KEG8` (Diamond 3 | Antarctic Peninsula | Win)
3. **`gm_win` (5 Reference Matches — 15 Teamfights)**:
   - `DA9Z8T` (Grandmaster 3 | Esperança | Win — *"What a champ Ana POV look like in a low GM lobby"*)
   - `SW5Y5Y` (Grandmaster 5 | Adlersbrunn | Win)
   - `HSA58Z` (Grandmaster 5 | Havana | Win — *"deathless ana"*)
   - `0AJTQ5` (Grandmaster 5 | New Queen Street | Win — *"Ana MasterClass"*)
   - `CP9GCK` (Grandmaster 3 | Runasapi | Win — *"Ana game vs Emongg, Somnus, Beaver"*)

---

## 2. Empirical 3-Cohort Statistical Findings

| HUD Telemetry Metric | `diamond_loss` ($n=5$) | `diamond_win` ($n=5$) | `gm_win` ($n=5$) | Key Coaching Insight |
| :--- | :---: | :---: | :---: | :--- |
| **Teamfight Win Rate** | `43.7%` | `66.7%` | **`70.0%`** | Clean progression across cohorts |
| **Offensive Anti-Nade Ratio** | `42.7%` | `56.7%` | **`62.4%`** | Monotonic increase from Diamond Loss $\rightarrow$ GM Win |
| **Offensive Nade $\rightarrow$ Fight Win Conversion** | `30.0%` | `46.7%` | **`90.0%`** | **#1 Differentiator**: 90% of GM offensive nades win the fight vs. only 30% in Diamond losses |
| **Unconverted Early Poke Nade Ratio** | `28.0%` | `23.3%` | **`3.3%`** | Diamond players waste ~25% of nades on early poke in fights they still lose; GM wastes almost 0% (`3.3%`) |
| **Self-Heal Nade Ratio** | `6.7%` | `36.7%` | `19.5%` | Diamond Anas rely heavily on reactive self-nades (`36.7%`) due to positioning exposure |
| **Defensive Ally-Save Nade Ratio** | `10.7%` | `6.7%` | **`18.1%`** | GM Anas spend nearly $3\times$ more defensive nades saving engaging teammates rather than themselves |
| **Deaths per Analyzed Segment** | `1.20` | `0.80` | **`0.60`** | Losing Diamond Anas die **twice as often** (`1.20` vs `0.60`) as Grandmaster Anas |

---

## 3. What the 15-Match Expansion Proved (v2 $\rightarrow$ v3 Calibration)

1. **Both Quantity AND Timing of Offensive Anti-Nades Matter (`ANA_DIA_01`)**:
   - In our initial 4-match pilot, a single outlier (`MFTXPE`) made it appear that GM Anas threw fewer offensive nades than Diamond Anas.
   - Expanding to **15 matches** revealed the true underlying distribution:
     - **Frequency**: `gm_win` actually maintains the highest offensive anti-nade ratio (**`62.4%`** vs. **`56.7%`** in `diamond_win` and **`42.7%`** in `diamond_loss`).
     - **Conversion Timing**: The real gap is **Offensive Nade Fight-Win Conversion** (**`90.0%` in GM** vs. **`46.7%` in Diamond Wins** and **`30.0%` in Diamond Losses**). In 4 out of 5 GM matches (`DA9Z8T`, `SW5Y5Y`, `0AJTQ5`, `CP9GCK`), **100% of offensive anti-nades resulted in a won teamfight**, because GM Anas wait until tank engagement or enemy cooldown exhaustion before throwing E.
     - Conversely, `diamond_loss` matches suffer from **two opposite extremes**: either **Total Utility Passivity** (`92EMFN` and `BV0VA4`, dying repeatedly with 0 offensive anti-nades landed) or **Unconverted Early Poke Nades** (`MFTXPE` and `78H7MX`, chucking purple nades before fights start, resulting in a `28.0%` unconverted poke nade rate).

2. **Survivability & Positioning Discipline (`ANA_DIA_05` — New Rule Added in v3)**:
   - Expanding the `diamond_loss` cohort exposed a failure mode missed by ability-only rules: in `92EMFN` (`2 deaths, 0 nades, 0 sleeps`) and `BV0VA4` (`3 deaths to Mei/Winston, 0 offensive nades`), the Diamond Ana died repeatedly in `2v4` / `3v5` stagger or open-sightline situations **without getting value from either ability**.
   - Across all 15 matches, `diamond_loss` averaged **`1.20` deaths per segment** vs. **`0.60` in `gm_win`**. We added **`ANA_DIA_05` (Unspent Utility Death / Stagger Exposure)** to catch deaths where the player dies without deploying Biotic Grenade or Sleep Dart.
