"use client";
import { useState } from "react";

interface Props {
  divAlgo: string;
  corrJitterEnabled: boolean;
  refinementEnabled: boolean;
  refinementMode: string;
}

/* ─── tiny reusable pieces ─── */
const H2 = ({ children }: { children: React.ReactNode }) => (
  <h2 className="text-sm font-semibold text-ag-text mt-4 mb-1.5 border-b border-ag-border pb-1">{children}</h2>
);
const H3 = ({ children }: { children: React.ReactNode }) => (
  <h3 className="text-xs font-semibold text-ag-accent mt-3 mb-1">{children}</h3>
);
const P = ({ children }: { children: React.ReactNode }) => (
  <div className="text-xs text-ag-text-muted leading-relaxed mb-1.5">{children}</div>
);
const Term = ({ name, children }: { name: string; children: React.ReactNode }) => (
  <div className="mb-2">
    <span className="text-xs font-semibold text-ag-text">{name}</span>
    <span className="text-xs text-ag-text-muted leading-relaxed"> — {children}</span>
  </div>
);

/* ─── collapsible section ─── */
function Section({ title, children, defaultOpen = false }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details open={open} onToggle={(e) => {
      // Only react to OUR toggle — ignore bubbled events from nested <details>
      if (e.target === e.currentTarget) setOpen((e.target as HTMLDetailsElement).open);
    }} className="group">
      <summary className="cursor-pointer text-xs font-semibold text-ag-text hover:text-ag-accent select-none py-1">
        <span className="ml-1">{title}</span>
      </summary>
      <div className="pl-3 pb-2">{children}</div>
    </details>
  );
}

/* ═══════════════════════════════════════════════════════════════════════ */
export default function OptimizerExplainer({ divAlgo, corrJitterEnabled, refinementEnabled, refinementMode }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <details open={open} onToggle={(e) => {
      // Only react to OUR toggle — ignore bubbled events from nested <details>
      if (e.target === e.currentTarget) setOpen((e.target as HTMLDetailsElement).open);
    }} className="text-xs mt-3">
      <summary className="cursor-pointer text-ag-text-muted hover:text-ag-text font-semibold text-sm">
        How It Works — Full Guide
      </summary>

      <div className="mt-2 p-4 bg-ag-dark/60 border border-ag-border rounded space-y-1 max-h-[80vh] overflow-y-auto">

        {/* ──────────────────── OVERVIEW ──────────────────── */}
        <H2>Overview</H2>
        <P>
          The Cross-Strategy Builder takes multiple backtested batches (each batch = one strategy tested across many assets)
          and finds the best combination of assets to trade together as a portfolio. The goal: pick assets that are individually
          profitable AND that don&apos;t all move together (so when one loses, others are winning).
        </P>
        <P>
          Think of it like building an all-star team. Each batch is a talent pool (e.g., &quot;Short strategy on 300 assets&quot;).
          The builder picks the best players across all pools, making sure they complement each other instead of all doing
          the same thing.
        </P>

        {/* ──────────────────── THE PIPELINE ──────────────────── */}
        <H2>The Pipeline — What Happens When You Click &quot;Build&quot;</H2>

        <Section title="Step 1 — Load Trades" defaultOpen>
          <P>
            The system pulls every trade from your selected batches. Each asset gets a column of daily PnL
            (how much it made or lost each day). If an asset appears in multiple batches (e.g., BTCUSDT in both Batch 138 and 141),
            it gets tagged with its batch ID so they&apos;re treated as separate &quot;players&quot; — because the same asset traded by
            different strategies will perform differently.
          </P>
          <P>
            Format: <code className="text-ag-accent">SYMBOL~B138_Short</code> means &quot;this asset, traded by batch 138, short side.&quot;
            This is why you might see the same symbol appear multiple times — each is a different strategy&apos;s version of trading that asset.
          </P>
        </Section>

        <Section title="Step 2 — Quality Filters">
          <P>
            Before the optimizer touches anything, weak assets are removed. This uses the filters you set in the controls above.
            Each filter is applied per-batch (so &quot;Min PF 1.5&quot; means every batch&apos;s assets must have PF ≥ 1.5, checked independently).
          </P>
          <Term name="Min Positions">
            Skip assets that had fewer than this many trades in the backtest. More trades = more statistical confidence
            that the results aren&apos;t just luck. Typical: 3–10. Set to 0 to disable.
          </Term>
          <Term name="Min PF (Profit Factor)">
            Skip assets where total wins ÷ total losses is below this. PF = 1.0 means break-even. PF = 2.0 means
            $2 won for every $1 lost. Typical: 1.2–2.0. Higher = stricter quality gate.
          </Term>
          <Term name="Min WR% (Win Rate)">
            Skip assets with a win rate below this percentage. A 50% win rate with good PF is fine (big winners offset small losses).
            Typical: 30–50%. Set to 0 to disable.
          </Term>
        </Section>

        <Section title="Step 3 — Stress Test Costs">
          <P>
            These add extra costs ON TOP of what the backtest already accounts for. They simulate real-world trading friction
            that might be worse than the backtest assumed. If a portfolio still looks good after adding these costs, it&apos;s more
            likely to survive in live trading.
          </P>
          <Term name="Extra Slippage %">
            How much the price moves against you between when you want to trade and when the order fills.
            This is per trade (entry + exit). Large-cap coins (BTC, ETH): 0.01–0.03%. Altcoins: 0.05–0.10%. Small-caps: 0.10%+.
            The extra slippage here is added to whatever slippage was already in the backtest.
          </Term>
          <Term name="Extra Commission %">
            Additional exchange fees per trade. Bybit maker: 0.01%, taker: 0.06%. This adds on top of what
            the backtest already charged.
          </Term>
          <Term name="Funding %/day">
            Daily funding cost for holding perpetual futures. On Bybit, you pay funding every 8 hours.
            Typical average: ~0.01–0.03%/day. This is charged based on position size × days held.
          </Term>
          <Term name="Spread %">
            The gap between the best bid and best ask price. You lose half the spread on entry and half on exit.
            Large-caps: 0.01%. Mid-caps: 0.02–0.05%. Small-caps: 0.05–0.10%.
          </Term>
        </Section>

        <Section title="Step 4 — Benchmark Robustness">
          <P>
            After filtering, each surviving asset is scored on &quot;benchmark robustness&quot; — how well does it perform
            compared to simply buying and holding the asset? An asset that beats buy-and-hold is genuinely adding
            alpha (value from the strategy), not just riding a bull market.
          </P>
          <P>
            Assets that fail the benchmark check (their strategy doesn&apos;t beat just holding the coin) are removed from the pool.
            The exact threshold depends on your settings. This step typically cuts the pool significantly
            (e.g., 152 assets → 81 in your logs).
          </P>
        </Section>

        <Section title="Step 5 — Seed Scores (Ranking Assets by Quality)">
          <P>
            Every surviving asset gets a &quot;seed score&quot; — a single number representing how good this asset is individually.
            The seed score is computed by rank-averaging across the metrics you selected in the Seed Ranking section below.
          </P>
          <P>
            <strong>How rank-averaging works:</strong> For each metric you selected (e.g., Sortino, Benchmark), all assets are
            ranked from best to worst. Then the ranks are averaged. If asset A is ranked #1 on Sortino and #5 on Benchmark,
            its average rank is 3.0. Lower average rank = better seed score.
          </P>
          <P>
            The top-ranked assets become &quot;seeds&quot; — starting points for the optimizer. The Max Seeds setting
            controls how many seeds are tried. More seeds = more starting points = more thorough search (but slower).
          </P>
          <P>
            <strong>Seed Prior Weight</strong> controls how much the optimizer cares about seed scores during asset selection.
            At 0.0 (default), the optimizer ignores quality entirely and only optimizes for the objective (e.g., max Sortino
            of the combined portfolio). At 0.15, the optimizer blends 85% objective + 15% seed quality. Higher values
            push toward picking individually strong assets even if they&apos;re somewhat correlated.
          </P>
        </Section>

        <Section title="Step 6 — Pool Filter">
          <P>
            The &quot;Pool per Batch&quot; setting caps how many assets from each batch enter the optimizer.
            If you have 150 qualified assets across 3 batches but set pool to 30, only the top 30 from each batch
            (by seed score) are used. This speeds up the optimizer and focuses on the best candidates.
          </P>
          <P>
            <strong>Expand Greedy Pool</strong> (checkbox): When ON, the optimizer can reach beyond the top seeds and
            pick ANY profitable asset in the pool. More thorough but slower. When OFF, it only picks from the top-ranked seeds.
          </P>
        </Section>

        <Section title="Step 7 — Correlation Matrix">
          <P>
            The optimizer needs to know how each asset moves relative to every other asset. This is the correlation matrix —
            a big grid where each cell says &quot;when asset A goes up, does asset B also go up (positive correlation),
            go down (negative), or do its own thing (near zero)?&quot;
          </P>
          <P>
            Correlation ranges from -1 (perfect opposites) to +1 (perfect twins). Two assets at +0.8 will lose money
            on the same days — bad for diversification. Two assets at 0.0 are independent — good.
          </P>
          <Term name="Standard Correlation">
            Compares daily PnL on every single day, including days when one or both assets had no open trades.
            On no-trade days, PnL is 0 — which can make things look less correlated than they really are.
          </Term>
          <Term name="Trade Overlap Correlation">
            Only compares days when BOTH assets had open trades. More accurate for strategies that trade infrequently
            (e.g., a strategy that&apos;s only in a position 30% of the time). This avoids the &quot;both at zero&quot; illusion.
            Better for most use cases but slower to compute.
          </Term>
        </Section>

        <Section title="Step 8 — The Optimizer (Algorithm Selection)" defaultOpen>
          <P>
            This is the core step. The optimizer takes the pool of qualified assets, their seed scores, and the correlation
            matrix, and selects which assets to include in your portfolio. The &quot;Algorithm&quot; dropdown controls HOW
            it makes this selection. Each algorithm has different strengths. See the &quot;Algorithms In Depth&quot; section below.
          </P>
          {corrJitterEnabled && (
            <P>
              <strong>Correlation Jitter is ON:</strong> Before each candidate portfolio is built, small random noise is added
              to the correlation matrix. This means each candidate &quot;sees&quot; slightly different correlations, so the algorithm
              explores different paths and produces more diverse candidates. The temperature controls how much noise
              (0.01 = tiny, 0.10 = significant). Think of it like running the draft multiple times with slightly different scouting reports.
            </P>
          )}
          {refinementEnabled && (
            <P>
              <strong>Refinement is ON ({refinementMode === "prune_and_swap" ? "Prune + Swap" : refinementMode === "prune" ? "Prune Only" : "Swap Only"}):</strong>{" "}
              After the algorithm builds each candidate portfolio, a refinement pass improves it.
              {refinementMode === "prune" || refinementMode === "prune_and_swap" ? (
                <> <strong>Prune:</strong> Overbuilds by ~40% more assets, then removes the weakest ones by checking each asset&apos;s
                marginal contribution (leave-one-out analysis). Assets that hurt the portfolio get cut.</>
              ) : null}
              {refinementMode === "prune_and_swap" ? <> Then, </> : null}
              {refinementMode === "swap" || refinementMode === "prune_and_swap" ? (
                <><strong>Swap:</strong> For each asset in the portfolio, tries replacing it with every asset NOT in the portfolio.
                If any swap improves the portfolio score, it&apos;s done. Repeats until no swap helps. This escapes &quot;local traps&quot;
                where the algorithm locked in a mediocre asset early.</>
              ) : null}
            </P>
          )}
        </Section>

        <Section title="Step 9 — Results & Marginal Contribution">
          <P>
            The optimizer returns multiple candidate portfolios (controlled by &quot;# Portfolios&quot;). Each candidate
            includes: the list of assets, combined equity metrics (PF, Sortino, Max DD, Net Profit, Stability),
            and a per-asset contribution breakdown (the Blueprint).
          </P>
          <P>
            <strong>The Blueprint (per-asset marginal contribution):</strong> For each asset in the portfolio, the system
            temporarily removes it and measures what happens to the portfolio. If removing an asset makes the portfolio worse,
            that asset is &quot;helpful.&quot; If removing it makes the portfolio better, that asset is &quot;harmful&quot;
            (the optimizer made a mistake keeping it). This is per-individual-asset, not per-batch.
          </P>
          <P>
            The Diversity filter (if enabled) ensures the returned candidates aren&apos;t too similar — measured by
            Jaccard overlap (what percentage of assets they share).
          </P>
        </Section>

        {/* ──────────────────── BUILD MODES ──────────────────── */}
        <H2>Build Modes</H2>

        <Section title="Auto (Combined / Flat)">
          <P>
            The simplest and most common mode. All batches are pooled together into one flat list, and the optimizer
            picks the best N assets from the entire pool at once. No layers, no groups — just one optimization pass.
            Best for most users.
          </P>
          <Term name="# Assets">
            How many assets to include in the final portfolio. Typical: 20–50. More assets = more diversification
            but thinner capital per asset.
          </Term>
          <Term name="Split Mode">
            When ON, you set separate counts for longs and shorts (e.g., 15 longs + 15 shorts).
            The optimizer picks the best longs and best shorts independently, then combines them.
          </Term>
          <Term name="Objective">
            What the optimizer tries to maximize when building the portfolio. See the &quot;Objectives&quot; section below.
          </Term>
          <Term name="Pool per Batch">
            Max assets to consider from each batch. Lower = faster but might miss good assets ranked lower.
          </Term>
        </Section>

        <Section title="Manual (3-Layer)">
          <P>
            Advanced mode for users with 3+ strategies. Uses a 3-layer beam search to preserve diversity across batches.
          </P>
          <P>
            Layer 1: Optimize within each batch independently (best assets per strategy). Layer 2: Group batches and
            find the best combination within each group. Layer 3: Combine the group winners into the final portfolio.
            Each layer has its own objective, letting you optimize differently at each level.
          </P>
          <P>
            This mode makes sure every strategy contributes something, rather than one dominant batch taking all the slots
            (which can happen in Auto mode).
          </P>
        </Section>

        <Section title="Directional Batches">
          <P>
            Designed for when you have separate Long and Short batches. Each batch is tagged as L (long) or S (short).
            The optimizer picks assets per-batch, then combines. Unlike Auto/Split, it carries multiple candidates forward
            through each layer so it doesn&apos;t &quot;pick a winner too early&quot; and throw away good options.
          </P>
        </Section>

        {/* ──────────────────── ALGORITHMS ──────────────────── */}
        <H2>Algorithms In Depth</H2>
        <P>
          The algorithm controls HOW the optimizer selects assets from the pool. Think of it as the &quot;draft strategy.&quot;
          All algorithms receive the same inputs (daily PnL, correlation matrix, seed scores, target size). They differ
          in how they make the picks.
        </P>

        <Section title="1. Iterative (Objective-Driven) — the default" defaultOpen>
          <P>
            <strong>How it works:</strong> Start with one &quot;seed&quot; asset (the best by seed score). Then, one at a time,
            try adding every remaining asset to the portfolio and measure the objective score (e.g., portfolio Sortino).
            Pick whichever addition improves the score the most. Repeat until you have N assets.
          </P>
          <P>
            <strong>Why it&apos;s good:</strong> Directly optimizes what you care about. If your objective is &quot;Max Sortino,&quot;
            every asset added genuinely improved the portfolio&apos;s Sortino. It&apos;s fast, flexible (supports all 25+ objectives),
            and the most customizable (seed prior weight, stability toggle, expand pool, etc.).
          </P>
          <P>
            <strong>The catch:</strong> It&apos;s a &quot;greedy&quot; algorithm — once it picks an asset, it never reconsiders.
            Asset #5 might have been the best choice at step 5, but after assets #6–#30 are added, #5 might actually
            be redundant. This is the &quot;no going back&quot; problem. Enable Refinement (Prune + Swap) to fix this.
          </P>
          <P>
            <strong>When to use:</strong> Best all-rounder. Use when you want to optimize for a specific PnL-based objective
            (Sortino, Calmar, Stability, etc.) and want maximum control over the settings.
          </P>
        </Section>

        <Section title="2. HRP (Hierarchical Risk Parity)">
          <P>
            <strong>How it works:</strong> Uses a clustering algorithm to group assets that behave similarly (like a
            family tree). Then picks assets from different branches of the tree so the portfolio spans different
            &quot;risk families.&quot; Assets are weighted by inverse volatility within each cluster.
          </P>
          <P>
            <strong>Why it&apos;s good:</strong> Doesn&apos;t need an objective function — it&apos;s purely structural. It looks at
            the correlation matrix and finds groups of related assets, then makes sure you have representation from each group.
            Works well when many assets are correlated (e.g., all altcoins moving together).
          </P>
          <P>
            <strong>The catch:</strong> Because it&apos;s purely structural, it might pick a diversified but mediocre portfolio
            (good diversification, poor individual quality). It also tends to converge — with small pools (&lt;100 assets),
            different seeds often produce the exact same portfolio, so you may see only 1 unique candidate instead of 3.
          </P>
          <P>
            <strong>When to use:</strong> When you have a large pool (100+ assets) and want maximum diversification
            without caring about a specific metric. Good for &quot;I just want assets that don&apos;t move together.&quot;
          </P>
        </Section>

        <Section title="3. PCA (Factor Clustering)">
          <P>
            <strong>How it works:</strong> Principal Component Analysis finds the main &quot;factors&quot; driving your pool&apos;s returns.
            Factor 1 might be &quot;the crypto market overall,&quot; Factor 2 might be &quot;DeFi vs. everything else,&quot;
            Factor 3 might be &quot;high-beta vs. low-beta.&quot; PCA picks one asset per factor — the asset most
            &quot;aligned&quot; with that factor.
          </P>
          <P>
            <strong>Why it&apos;s good:</strong> Maximum statistical independence — each asset represents a different
            source of returns. If the factors are real and persistent, this gives the best diversification.
          </P>
          <P>
            <strong>The catch:</strong> PCA factors are abstract math — they don&apos;t always correspond to things you&apos;d
            recognize. Factor 3 might capture a temporary correlation that won&apos;t persist in live trading.
            Same convergence issue as HRP with small pools.
          </P>
          <P>
            <strong>When to use:</strong> When you have a large pool and want statistically independent bets.
            Good for academic rigor. Pairs well with Correlation Jitter to produce more diverse candidates.
          </P>
        </Section>

        <Section title="4. Quality First">
          <P>
            <strong>How it works:</strong> Flips the usual approach. Instead of &quot;find the most diversified portfolio&quot;
            (and hope quality follows), it starts with the TOP quality assets (ranked by seed score) and then removes
            the most correlated pairs until the target size is reached. When removing a correlated pair, it keeps the
            higher-quality member.
          </P>
          <P>
            <strong>Why it&apos;s good:</strong> Guarantees high individual quality. Every asset in the portfolio earned
            its spot on merit first. Correlation is a constraint (&quot;no two assets above 0.7 correlation&quot;), not the objective.
            This means you never sacrifice a great asset just because it&apos;s mildly correlated with another great asset.
          </P>
          <P>
            <strong>The catch:</strong> If your top assets are all highly correlated (e.g., all altcoins moving with BTC),
            the pruning step might cut a lot of good ones. The final portfolio could be smaller than intended if the
            pool lacks diversity at the top.
          </P>
          <P>
            <strong>When to use:</strong> When your batches produce consistently good individual results (high PF, high Sortino)
            and you trust the individual quality. Best when you&apos;d rather have 20 great assets with moderate diversification
            than 50 mediocre-but-uncorrelated ones.
          </P>
        </Section>

        <Section title="5. Tournament Draft">
          <P>
            <strong>How it works:</strong> Inspired by genetic algorithms. Instead of deterministically picking the single
            best next asset (like Iterative), it randomly samples 5 candidates, scores each on a blend of quality + correlation
            contribution, picks the best of those 5. Repeats until the portfolio is full. Then does this entire process
            200 times with different random samples and keeps the best overall portfolio.
          </P>
          <P>
            <strong>Why it&apos;s good:</strong> Explores much more of the search space. Iterative always makes the same picks
            from the same seed — it&apos;s deterministic. Tournament is stochastic: 200 different &quot;drafts&quot; means 200 different
            portfolios are tested. The randomness helps escape local optima (situations where the Iterative would get stuck).
          </P>
          <P>
            <strong>The catch:</strong> Slower than Iterative (200× more work). Results vary slightly between runs
            (different random seeds). The quality-vs-diversity blend is a fixed formula — you can&apos;t tune the objective
            as flexibly as with Iterative.
          </P>
          <P>
            <strong>When to use:</strong> When Iterative gives inconsistent or mediocre results and you want to explore
            more possibilities. Good &quot;second opinion&quot; algorithm. Also good when you suspect the Iterative is stuck in a
            local optimum.
          </P>
        </Section>

        <Section title="6. Marginal Sharpe">
          <P>
            <strong>How it works:</strong> For each candidate asset, computes its marginal contribution to the portfolio&apos;s
            Sharpe ratio: how much does adding this one asset improve the portfolio&apos;s risk-adjusted return?
            The formula: <code className="text-ag-accent">marginal_sharpe = (asset_return − portfolio_return × correlation) / portfolio_vol</code>.
            Always adds the asset with the highest marginal Sharpe. No separate &quot;objective&quot; needed — the marginal Sharpe
            IS the objective.
          </P>
          <P>
            <strong>Why it&apos;s good:</strong> Naturally balances quality AND diversification in one number. A high-return
            asset that&apos;s correlated with the portfolio gets penalized. A moderate-return asset that&apos;s uncorrelated gets
            rewarded. This is what institutional portfolio managers use. No tuning knobs — the math handles the tradeoff.
          </P>
          <P>
            <strong>The catch:</strong> Assumes returns are normally distributed (they&apos;re not, especially in crypto).
            Sharpe penalizes upside volatility equally with downside — a &quot;good&quot; volatile asset might get rejected.
            Works best with lots of daily data (200+ days).
          </P>
          <P>
            <strong>When to use:</strong> When you want a principled, math-driven approach with no arbitrary parameters.
            Best for users who trust the Sharpe ratio as their north star metric. Clean, simple, institutional.
          </P>
        </Section>

        <Section title="7. Ensemble Consensus">
          <P>
            <strong>How it works:</strong> Runs 3 methods internally (Iterative with hybrid objective, Iterative with stability
            objective, and HRP). Each produces a top portfolio. Then counts how many times each asset appears across all 3.
            Assets appearing in 2 or 3 out of 3 are &quot;consensus picks&quot; — they&apos;re good no matter how you look at it.
            The final portfolio is built starting with consensus picks, then fills remaining slots with the best available.
          </P>
          <P>
            <strong>Why it&apos;s good:</strong> Most robust. No single algorithm&apos;s blind spot can dominate. If an asset
            is only liked by one method, it&apos;s probably an artifact of that method&apos;s bias. If all 3 agree, it&apos;s
            genuinely good. This is the &quot;wisdom of crowds&quot; approach.
          </P>
          <P>
            <strong>The catch:</strong> Slowest option (3× the work). The consensus might be conservative — truly great
            niche assets that only one method can find will be ranked lower. Also, if all 3 internal methods have the
            same blind spot, the ensemble inherits it.
          </P>
          <P>
            <strong>When to use:</strong> When you can&apos;t decide which method to trust, or when you want maximum
            confidence in the result. Good for final portfolio construction where you&apos;ll actually trade the result.
          </P>
        </Section>

        {/* ──────────────────── OBJECTIVES ──────────────────── */}
        <H2>Objectives — What the Optimizer Maximizes</H2>
        <P>
          The objective controls what the Iterative algorithm tries to maximize when deciding which asset to add next.
          For other algorithms (HRP, PCA, Quality First, Tournament, Marginal Sharpe, Ensemble), the objective
          has less impact since they use their own scoring logic — but it still affects the final ranking of candidates.
        </P>

        <Section title="Core Objectives">
          <Term name="Stability (Equity Quality)">
            Measures how smooth the portfolio&apos;s equity curve is. A straight diagonal line going up = stability 1.0.
            A jagged, volatile curve = stability near 0. This is the best all-rounder objective because it implicitly
            captures drawdown, consistency, and growth. Specifically, it&apos;s the R² of a linear regression on the
            cumulative PnL — how well the equity curve fits a straight line.
          </Term>
          <Term name="Smart Balance (Hybrid)">
            A blended score that considers correlation structure (how diversified the portfolio is) AND individual
            asset performance. The formula weights: average pairwise correlation, absolute correlation, and stability.
            Good when you want both diversification and quality without committing to one.
          </Term>
          <Term name="Uncorrelated (Diversity)">
            Purely minimizes the average correlation between assets. Doesn&apos;t care about profit at all during selection —
            just finds assets that move independently. Quality only matters in the final ranking of candidates.
            Use when you have a huge pool of profitable assets and want maximum diversification.
          </Term>
          <Term name="Anti-Correlated (Hedge)">
            Looks for assets that move in OPPOSITE directions. When one wins, the other loses. This creates natural
            hedges — the portfolio is protected against directional moves. Rare in crypto (most things go up/down together),
            but powerful when available. Best combined with long + short strategies.
          </Term>
        </Section>

        <Section title="Risk-Adjusted Objectives">
          <Term name="Max Sortino">
            Maximizes return per unit of DOWNSIDE risk. Unlike Sharpe, Sortino only penalizes negative volatility.
            A strategy that makes big gains some days and small gains other days gets a great Sortino (big upside
            isn&apos;t penalized) but a mediocre Sharpe. Best for crypto where upside vol is the whole point.
          </Term>
          <Term name="Max Sharpe">
            Maximizes return per unit of TOTAL volatility (both up and down). The classic academic metric.
            Penalizes upside volatility the same as downside — which can reject great trend-following strategies
            that have large winning days. Use when you genuinely want low total volatility.
          </Term>
          <Term name="Max Calmar">
            Annual return ÷ worst drawdown. Answers: &quot;how much return did I get per unit of maximum pain?&quot;
            A Calmar of 3.0 means you made 3× your worst drawdown in annual return. Good for measuring capital efficiency.
          </Term>
          <Term name="Max Recovery">
            Total profit ÷ worst drawdown. Similar to Calmar but uses total profit instead of annual.
            Higher = earns back losses faster. Good for evaluating how quickly a portfolio bounces back from dips.
          </Term>
          <Term name="Min Ulcer Index">
            Penalizes both DEEP and LONG-LASTING drawdowns. A portfolio that drops 20% for 1 day is better
            than one that drops 10% for 30 days (less &quot;pain&quot; to hold). Named because drawdowns give you ulcers.
            Use when psychological comfort matters.
          </Term>
          <Term name="Max Robust Sortino">
            Sortino adjusted for extreme moves. Standard Sortino can be thrown off by one huge winning day.
            Robust Sortino trims outliers before calculating. Better for crypto&apos;s wild price swings.
          </Term>
        </Section>

        <Section title="Drawdown & Recovery Objectives">
          <Term name="Min Drawdown">
            Minimizes the worst peak-to-trough drop. The portfolio that never dips much. Very conservative —
            might sacrifice a lot of upside to avoid any significant dip. Use when capital preservation is #1.
          </Term>
          <Term name="Min Lake Ratio">
            Minimizes the fraction of time the equity is below its all-time high. A lake ratio of 0.1 means
            the equity was at or near its peak 90% of the time. Use when you hate seeing red on your portfolio page.
          </Term>
          <Term name="Max Rapid Recovery">
            Fastest bounce-back from drawdowns. Favors portfolios that recover from dips quickly rather than grinding
            slowly back. Good when you need confidence that bad periods are short-lived.
          </Term>
        </Section>

        <Section title="Profitability Objectives">
          <Term name="Max Profit Factor">
            Total wins ÷ total losses. PF = 2.0 means $2 gained for every $1 lost. Simple, robust, hard to game.
            Use when you want raw profitability without adjusting for risk.
          </Term>
          <Term name="Max Expectancy">
            Average profit per trade. If your average trade makes $50, expectancy = $50. Higher = bigger edge
            per trade. Useful when comparing strategies with different trade frequencies.
          </Term>
          <Term name="Max Payoff Ratio">
            Average win ÷ average loss. A payoff of 2.0 means winners are twice as big as losers.
            Great for trend-following strategies where you want large winners and small stop-losses.
          </Term>
          <Term name="Max Gain/Pain">
            Total gains ÷ total pain (sum of all drawdown periods). Works better than Sharpe for crypto
            because it focuses on actual PnL rather than daily returns. Good with few trades.
          </Term>
          <Term name="Max CDE">
            Conditional Drawdown at Expectation — controls the worst drawdowns. Catches portfolios that look
            good on average but have ugly tail events. Use when you care about &quot;how bad can it really get?&quot;
          </Term>
          <Term name="Max Tail Ratio">
            Positive skew metric. Measures if your best outcomes are much bigger than your worst outcomes.
            A high tail ratio means big wins, small losses — the asymmetric payoff profile that good trading seeks.
          </Term>
          <Term name="Max EST Efficiency">
            How efficiently the portfolio converts time into returns. Higher = better use of your capital.
            Penalizes portfolios that sit idle or make money inefficiently.
          </Term>
        </Section>

        <Section title="Structural Quality Objectives">
          <Term name="Max GPR (Gain-to-Pain Ratio)">
            Similar to Gain/Pain but on portfolio equity. Rewards smooth, consistent growth.
            Good general-purpose metric that&apos;s more stable than Sortino.
          </Term>
          <Term name="Max TER (Trend Efficiency Ratio)">
            How straight the equity curve trends upward. High TER = clean, directional growth.
            Low TER = choppy, sideways action. Best for trend-following portfolios.
          </Term>
          <Term name="Max KeepScore">
            Combined trend quality score. Blends multiple structural metrics into one number.
            Picks the portfolio that trends most cleanly overall.
          </Term>
          <Term name="Max MR Score">
            Mean-reversion quality score. Best for portfolios designed around mean-reversion strategies
            where equity should oscillate around its trend rather than trend sharply.
          </Term>
          <Term name="Max Crossing Rate">
            How often equity crosses its moving average. High crossing rate = good mean-reversion behavior.
            Signals that drawdowns are temporary and equity keeps returning to trend.
          </Term>
        </Section>

        {/* ──────────────────── SEED METRICS ──────────────────── */}
        <H2>Seed Metrics — How Individual Assets Are Ranked</H2>
        <P>
          Before the optimizer runs, each asset is scored and ranked. The metrics you select in &quot;Seed Ranking&quot;
          determine this ranking. The optimizer uses these rankings to decide which assets to try first (seeds)
          and optionally as a quality signal during selection (via Seed Prior Weight).
        </P>
        <P>
          You can select multiple metrics — they&apos;re combined by rank-averaging (each metric ranks the assets
          independently, then the ranks are averaged). This prevents any single metric from dominating.
          For example, an asset that&apos;s #1 on Sortino but #50 on Stability will have an average rank of 25.5.
        </P>

        <Section title="Common Seed Metrics">
          <Term name="Sortino">Ranks by individual Sortino ratio. Good general-purpose seed metric. Favors assets with high return and low downside risk.</Term>
          <Term name="Benchmark">Ranks by how much the asset&apos;s strategy beats buy-and-hold. Filters out &quot;riding the market&quot; assets. One of the most important seeds.</Term>
          <Term name="Profit Factor">Ranks by PF. Simple profitability ranking.</Term>
          <Term name="Stability">Ranks by R² of equity curve. Favors smooth, consistent performers.</Term>
          <Term name="Net Profit">Ranks by total dollar profit. Simple but can be misleading if position sizes differ.</Term>
          <Term name="Win Rate">Ranks by percentage of winning trades. Consistency metric.</Term>
          <Term name="Max Drawdown">Ranks by smallest drawdown. Conservative — favors assets that never dip much.</Term>
          <Term name="IS-WFE">Walk-Forward Efficiency — how well in-sample performance predicts out-of-sample. Higher = more likely to work in live trading.</Term>
          <Term name="Asset Quality">Structural quality grade (A through F) based on equity curve properties like Hurst exponent and trend efficiency.</Term>
          <Term name="Strategy Quality">Similar to Asset Quality but aggregated at the strategy level.</Term>
          <Term name="Avg Range">Performance across different market conditions (bull, bear, sideways). Rewards assets that work in all environments.</Term>
        </Section>

        {/* ──────────────────── OTHER SETTINGS ──────────────────── */}
        <H2>Other Settings</H2>

        <Section title="Correlation Jitter">
          <P>
            When enabled, adds small random noise to the correlation matrix before each candidate portfolio is built.
            This means each of the N candidates sees a slightly different correlation landscape, leading to different
            asset selections and more diverse final candidates.
          </P>
          <Term name="Temperature (0.01–0.10)">
            Controls noise intensity. 0.01 = barely noticeable differences between candidates. 0.03 = moderate diversity
            (recommended starting point). 0.10 = significant variation — candidates will differ substantially.
            Too high can produce nonsensical correlations. Start at 0.03 and increase if you want more variety.
          </Term>
          <P>
            <strong>Technical detail:</strong> Uniform noise of ±temperature is added to each off-diagonal correlation value.
            The matrix is then forced to remain valid (symmetric, positive semi-definite, diagonal = 1.0).
          </P>
        </Section>

        <Section title="Refinement (Post-Construction Improvement)">
          <P>
            A second pass that improves the portfolio AFTER the algorithm builds it. Addresses the &quot;greedy&apos;s no going back&quot;
            problem. Think of it as a coaching session after the draft — you review each player and make trades.
          </P>
          <Term name="Prune Only">
            Overbuilds the portfolio by ~40% more assets than needed, then runs leave-one-out analysis on each asset.
            Any asset that hurts the portfolio (removing it improves the score) gets cut. Repeats until at target size.
            Good for removing deadweight.
          </Term>
          <Term name="Swap Only">
            For each asset in the portfolio, tests replacing it with every asset NOT in the portfolio. If any swap
            improves the portfolio score, it&apos;s executed. Repeats until no swap helps (hill climbing to a local optimum).
            Good for upgrading mediocre picks.
          </Term>
          <Term name="Prune + Swap">
            Does both: prune first (remove the bad), then swap (upgrade the rest). Most thorough but slowest.
            This is the recommended setting when you want the best possible result and don&apos;t mind the extra time.
          </Term>
        </Section>

        <Section title="Prioritize Stability">
          <P>
            When ON, the optimizer weighs drawdown and volatility more heavily during asset selection.
            Assets that would cause large portfolio drawdowns are penalized even if they boost returns.
            Use when you want a smoother ride over maximum profit.
          </P>
        </Section>

        <Section title="Diversity (Candidate Overlap)">
          <P>
            Controls how different the returned candidate portfolios must be from each other. Measured by Jaccard
            overlap: the percentage of assets two candidates share.
          </P>
          <Term name="Off">No constraint — candidates can be identical.</Term>
          <Term name="Low (70%)">Candidates must differ by at least 30% of assets.</Term>
          <Term name="Medium (50%)">Candidates must differ by at least 50%. Good default.</Term>
          <Term name="High (30%)">Candidates must be quite different — at most 30% overlap.</Term>
          <Term name="Max (10%)">Candidates must be almost completely different sets of assets.</Term>
        </Section>

        <Section title="Expand Greedy Pool">
          <P>
            <strong>ON (default):</strong> The optimizer can pick ANY profitable asset from the pool, even if it wasn&apos;t
            a top seed. The seed ranking determines starting points, but during the greedy pass, all assets are considered
            as candidates. More thorough — might find hidden gems ranked #40 that happen to complement the portfolio perfectly.
          </P>
          <P>
            <strong>OFF:</strong> Only picks from the top-ranked seeds (Max Seeds count). Faster and more predictable,
            but might miss assets that are great for diversification despite lower individual quality.
          </P>
        </Section>

        <Section title="Streamlit Logic (Pure Correlation)">
          <P>
            Legacy compatibility mode. When ON, the optimizer picks assets based ONLY on correlation (ignoring PnL
            during selection). This matches the behavior of the original Streamlit Dashboard. Profit scoring only
            happens in the final ranking step. Not recommended for most users — the modern objectives are better.
          </P>
        </Section>

        <Section title="Max DD Filter">
          <P>
            When set above 0, rejects any candidate portfolio whose worst drawdown exceeds this percentage of peak equity.
            For example, Max DD = 30% means any portfolio that ever dropped more than 30% from its high is rejected entirely.
            Applied after the optimizer runs, as a final safety gate.
          </P>
        </Section>

        <Section title="Seed Prior Weight">
          <P>
            Controls how much the Iterative algorithm considers individual asset quality (seed scores) during its
            greedy selection process.
          </P>
          <P>
            <strong>0.0 (default):</strong> Pure objective optimization. The algorithm only cares about improving
            the portfolio&apos;s objective score (e.g., Sortino). An individually mediocre asset that happens to
            be uncorrelated will be preferred over a great asset that&apos;s correlated.
          </P>
          <P>
            <strong>0.05–0.15:</strong> Mild quality bias. Ties are broken in favor of higher-quality assets.
            Still primarily optimizes the objective but gives a gentle nudge toward quality.
          </P>
          <P>
            <strong>0.20–0.30:</strong> Strong quality bias. The optimizer actively prefers individually good
            assets even if they&apos;re somewhat more correlated. Use when your pool has many assets with similar
            correlation profiles and you want the best among equals.
          </P>
        </Section>

        {/* ──────────────────── OVERFITTING GUARD ──────────────────── */}
        <H2>Overfitting Guard — Detecting &quot;Too Good to Be True&quot;</H2>
        <P>
          The Overfitting Guard is a set of statistical filters that prevent the optimizer from
          picking assets that look amazing in-sample but won&apos;t hold up in live trading. The core
          insight: with 5–25 trades per asset, extreme metrics (PF 8+, Sortino 5+, WR 90%+,
          near-zero drawdown) are almost always statistical flukes, not genuine edges.
        </P>
        <P>
          Each approach tackles overfitting from a different angle. They can be combined (they
          chain together) or used individually. When the master toggle is ON, enabled approaches
          are applied in order: Diminishing Returns → Z-Score → Bayesian Shrinkage → Metric
          Capping → Normal Range Bonus. After the optimizer runs, Walk-Forward Validation
          re-ranks the final candidates.
        </P>

        <Section title="1. Diminishing Returns (Log Transform)">
          <P>
            <strong>How it works:</strong> Compresses extreme metric values logarithmically.
            PF going from 1.5→2.5 is a big score improvement, but PF going from 5→8 barely changes the score.
            This reduces the &quot;pull&quot; of suspiciously perfect assets without removing them from the pool.
          </P>
          <P>
            <strong>Why it helps:</strong> An asset with PF 8.0 from 10 trades is probably just lucky.
            Log compression ensures it doesn&apos;t dominate the rankings over a more believable PF 2.5.
          </P>
          <P>
            <strong>When to use:</strong> Always a good first line of defense. Low computational cost,
            no trade-count dependency. Works on all metrics.
          </P>
          <P>
            <strong>Parameters:</strong> Each metric has a &quot;believable ceiling&quot; cap. Values at or above
            the cap get heavily compressed. Lower cap = more aggressive. Default PF cap = 4.0 means
            PF above 4 barely improves the score.
          </P>
        </Section>

        <Section title="2. Z-Score Suspicion Penalty">
          <P>
            <strong>How it works:</strong> Compares each asset to its own batch average. If most
            assets in a batch have PF 1.5–3.5 and one has PF 9.0, it&apos;s flagged as suspicious.
            The penalty increases progressively — the more extreme the outlier, the bigger the penalty.
          </P>
          <P>
            <strong>Why it helps:</strong> Catches outliers relative to what the strategy &quot;normally&quot;
            produces. A PF of 4.0 might be fine in a high-quality batch but suspicious in a mediocre one.
          </P>
          <P>
            <strong>Parameters:</strong> <Term name="Threshold">How many standard deviations above the
            batch mean before flagging (1.5 = aggressive, 2.5 = mild).</Term>{" "}
            <Term name="Strength">Penalty per extra standard deviation beyond threshold (0.10 = gentle,
            0.50 = harsh).</Term>
          </P>
        </Section>

        <Section title="3. Bayesian Shrinkage">
          <P>
            <strong>How it works:</strong> Pulls extreme metrics toward the batch average. The pull
            depends on trade count: fewer trades = more pull (less confidence), more trades = less pull.
            PF 8.0 from 5 trades gets pulled to ~5.25. PF 8.0 from 100 trades barely changes.
          </P>
          <P>
            <strong>Why it helps:</strong> This is the most trade-count-adaptive approach. It automatically
            adjusts skepticism based on sample size. Assets near the batch mean are barely affected.
          </P>
          <P>
            <strong>Parameters:</strong> <Term name="k (shrinkage strength)">k=3: light (trusts raw data more).
            k=5: moderate (recommended for 5–25 trades). k=10+: strong (for very small samples).</Term>
          </P>
        </Section>

        <Section title="4. Metric Capping (Sigmoid)">
          <P>
            <strong>How it works:</strong> A hard ceiling using a tanh S-curve. Everything above the cap
            scores nearly the same — PF 5 and PF 10 become indistinguishable. Different from Log Transform:
            sigmoid is sharper near the cap and flatter after it.
          </P>
          <P>
            <strong>Why it helps:</strong> When you want a true &quot;wall&quot; at the cap value. Log transform
            still gives some credit for going from PF 8→12; sigmoid doesn&apos;t.
          </P>
          <P>
            <strong>Parameters:</strong> Same per-metric caps as Log Transform. Can use both — log compresses
            first, then sigmoid walls off what&apos;s left.
          </P>
        </Section>

        <Section title="5. Normal Range Bonus (Inverted-U)">
          <P>
            <strong>How it works:</strong> Defines a &quot;healthy&quot; percentile range within the pool.
            Assets inside get a bonus. Assets outside (too good OR too bad) get penalized.
            Specifically powerful for Max DD — assets with suspiciously low drawdown are penalized.
          </P>
          <P>
            <strong>Why it helps:</strong> Directly addresses the user-observed problem: portfolios with
            the smallest drawdown tend to overfit the most. This approach rewards &quot;normal&quot; drawdown
            and penalizes near-zero DD.
          </P>
          <P>
            <strong>Parameters:</strong> <Term name="Low/High %">The percentile boundaries of the normal range
            (20–60% default).</Term> <Term name="Scale">Penalty steepness for outliers (0.3 default).</Term>{" "}
            <Term name="Bonus">Extra credit for being in the sweet spot (0.1 = 10% boost).</Term>
          </P>
        </Section>

        <Section title="6. Walk-Forward Validation (Portfolio-Level)">
          <P>
            <strong>How it works:</strong> After the optimizer builds candidates using all IS data,
            this re-ranks them by how well they perform on held-out data (last 30% of the IS period).
            Overfitted portfolios degrade on the validation window; robust ones hold up.
          </P>
          <P>
            <strong>Why it helps:</strong> This is the gold standard for overfitting detection. It
            works at the portfolio level (where you have enough combined trades) rather than per-asset.
            A portfolio of 30 assets with 5–25 trades each has 150–750 combined trades — statistically meaningful.
          </P>
          <P>
            <strong>Parameters:</strong> <Term name="Split %">Train/validate split (70% = build on first 70%,
            validate on last 30%).</Term> <Term name="Metric">Which metric ranks candidates on validation
            (Stability recommended).</Term>
          </P>
          <P>
            <strong>Two modes:</strong> The &quot;Re-rank by WF Validation&quot; toggle works as a post-filter
            with any algorithm. The &quot;Walk-Forward Validated&quot; algorithm in the dropdown is a full
            meta-algorithm that only lets the optimizer see 70% of the data.
          </P>
        </Section>

        {/* ──────────────────── SETTINGS RECOMMENDER ──────────────────── */}
        <H2>Settings Recommender (Auto-Tuner)</H2>
        <Section title="What is it?" defaultOpen>
          <P>
            The Settings Recommender analyses your IS batch data — trade count distributions,
            metric distributions per batch, correlation structure, suspicious outlier counts — and
            proposes optimal optimizer settings. It <strong>never touches OOS data</strong>, so
            there is no data leakage. Think of it as a knowledgeable assistant that looks at your
            pool characteristics and says &ldquo;given what I see, here are the best settings.&rdquo;
          </P>
        </Section>
        <Section title="Quick Mode">
          <P>
            Runs instantly. Profiles your pool and applies heuristic rules calibrated for crypto
            backtesting pools with 5–25 trades/asset. Analyses trade count median, PF skewness,
            correlation density, quality Gini coefficient, batch balance, and suspicious outlier
            counts. Returns one recommendation per setting, each with a confidence score and a
            plain-English reason explaining <em>why</em> that value was chosen.
          </P>
          <P>
            <strong>What it recommends:</strong> algorithm choice, objective, seed prior weight,
            which overfitting guard approaches to enable (with specific parameter values), refinement
            mode, correlation jitter, seed metrics, and quality filters. You can apply each
            recommendation individually or hit &ldquo;Apply All&rdquo; to use the full preset.
          </P>
        </Section>
        <Section title="Thorough Mode (Cross-Validation Sweep)">
          <P>
            Takes several minutes. Builds a grid of setting combinations (4 algorithms × 5 objectives ×
            3 seed prior weights × 5 guard presets × 4 filter levels, capped at 500 combos), then
            runs walk-forward cross-validation for each: builds the portfolio on 65%/70%/75% of the
            IS data, scores the portfolio on the remaining validation slice, and averages across all
            three splits.
          </P>
          <P>
            <strong>Scoring:</strong> each combination gets a composite score based on (a) average
            validation stability, (b) overfit ratio (validation stability ÷ training stability — closer
            to 1.0 = less overfitted), and (c) consistency bonus for low variance across splits.
            The top 5 presets are shown in a table. You can inspect their validation PF, stability,
            overfit ratio, and apply any preset with one click.
          </P>
        </Section>
        <Section title="Pool Profile Stats">
          <P>
            Both modes display a pool profile with key stats: total assets, batch count, L/S split,
            median trade count, % below 10 trades (warning flag), average |correlation|, % pairs
            above 0.50 (high-correlation warning), PF spread (p90−p10), quality Gini (inequality
            of asset quality), batch balance ratio, and suspicious asset count. Yellow highlights
            indicate areas of concern.
          </P>
        </Section>
        <Section title="Suspicious Assets">
          <P>
            Assets flagged as &ldquo;suspicious&rdquo; have 3+ metrics that are 2σ+ above their
            batch mean — the classic &ldquo;too good to be true&rdquo; signal. These are listed
            by name so you can inspect them. The recommender uses this count to calibrate how
            aggressive the overfitting guard should be.
          </P>
        </Section>
        <Section title="When to use which mode?">
          <P>
            <strong>Quick:</strong> Use when exploring or when you want a fast starting point.
            Good enough for most cases, especially if you already have some intuition about your data.
            <strong>Thorough:</strong> Use before a final production run, when you want to be sure the
            settings are optimal and not just &ldquo;sensible defaults.&rdquo; The CV sweep finds
            non-obvious combinations (e.g., HRP + aggressive guard outperforming greedy + light guard)
            that heuristics might miss.
          </P>
        </Section>

        {/* ──────────────────── TIPS ──────────────────── */}
        <H2>Tips & Recommendations</H2>
        <Section title="Which algorithm should I start with?" defaultOpen>
          <P>
            Start with <strong>Iterative (Objective-Driven)</strong> with objective = Stability and Seed Prior Weight = 0.10–0.15.
            Enable Refinement (Prune + Swap). This gives you a quality-aware, objective-optimized portfolio with
            post-hoc improvement. If results feel too homogeneous, try Tournament or Ensemble as a second opinion.
          </P>
        </Section>
        <Section title="My candidates all look the same">
          <P>
            Turn on Correlation Jitter (temperature 0.03–0.05) and set Diversity to Medium or High.
            If using HRP or PCA, these are especially prone to convergence with small pools — jitter helps the most here.
          </P>
        </Section>
        <Section title="The optimizer picks mediocre assets">
          <P>
            Increase Seed Prior Weight to 0.10–0.20. Try Quality First algorithm (puts quality above diversification).
            Or use Refinement with Prune + Swap to replace weak assets after construction.
          </P>
        </Section>
        <Section title="I want maximum confidence in the result">
          <P>
            Use Ensemble Consensus — it runs 3 methods and only keeps assets that multiple methods agree on.
            Add Refinement (Prune + Swap) for extra polish. Slowest option but most robust.
          </P>
        </Section>
        <Section title="My portfolios look great in-sample but fail OOS">
          <P>
            This is classic overfitting. Enable the Overfitting Guard with at least Diminishing
            Returns + Z-Score Suspicion. If you have very few trades (5–15), also enable Bayesian
            Shrinkage with k=5–10. For maximum protection, turn on Walk-Forward Re-rank — it
            validates each candidate portfolio on held-out data.
          </P>
        </Section>
      </div>
    </details>
  );
}
