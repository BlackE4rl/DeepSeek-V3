"""Generate a synthetic market and its universe file.

This exists so the whole pipeline can be exercised without network access. The
prices are simulated: any performance number computed from them is evidence
about the code, not about the strategy.
"""

import argparse
import os

from ..datasets import synthetic
from ._common import write_manifest


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="directory to write the data into")
    parser.add_argument("--seed", type=int, default=42, help="master RNG seed")
    parser.add_argument("--start", default="2005-01-03", help="first date")
    parser.add_argument("--end", default="2026-06-30", help="last date")
    parser.add_argument(
        "--account-currency", default="EUR", help="currency of the generated universe"
    )
    args = parser.parse_args()

    manifest = synthetic.make_universe(
        args.out, seed=args.seed, start=args.start, end=args.end
    )
    universe_path = os.path.join(args.out, "universe.yaml")
    with open(universe_path, "w", encoding="utf-8") as handle:
        handle.write(synthetic.synthetic_universe_yaml(args.account_currency))

    write_manifest(args.out, manifest)

    print(f"wrote {len(manifest['symbols'])} price files to {os.path.join(args.out, 'ohlcv')}")
    print(f"wrote universe definition to {universe_path}")
    print(
        "reminder: these prices are simulated. Results from them prove the code "
        "runs and nothing else."
    )


if __name__ == "__main__":
    main()
