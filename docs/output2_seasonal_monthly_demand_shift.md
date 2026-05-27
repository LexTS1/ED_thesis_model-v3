# Output 2: Seasonal And Monthly Demand Shift

Output 2 tests whether climate change only reduces annual heating demand, or
also changes when residential demand and comfort pressure occur.

The dedicated comparison outputs are:

- `experiments/scenario_tree/summaries/comparison_level/monthly_demand_shift_comparison.csv`
- `experiments/scenario_tree/summaries/comparison_level/seasonal_demand_shift_comparison.csv`

The monthly and seasonal tables report useful space-heating demand, gross
electricity demand, grid import, gas demand, total final energy, CDD 22, excess
heat, overheating hours, and indoor-temperature exceedance degree-hours. Active
cooling final energy is not included.

Current audit note:

The climate-only annual useful-heating comparison should be treated with
caution. The climate metrics show the expected HDD decrease in warmer future
windows, but some useful-heating rows increase. The latest audit indicates this
is not thesis-ready evidence yet because baseline and future frozen-stock rows
can mix different run provenance/model-input hashes. Use the HDD/CDD direction
and the seasonal diagnostic tables as context, but do not foreground exact
annual useful-heating percentage deltas until the baseline and future
frozen-stock leaves are rerun from one frozen clean revision.

Season definitions are fixed:

- winter: December, January, February
- spring: March, April, May
- summer: June, July, August
- autumn: September, October, November
- shoulder: spring plus autumn
- annual: all months

Thesis interpretation:

Climate change can reduce total annual useful heating demand while also shifting
when demand occurs. Winter heating demand decreases but remains peak-critical,
and shoulder-season heating can decline strongly. At the same time, summer
cooling pressure increases through CDD 22, overheating hours, excess heat, and
indoor comfort exceedance. The result should therefore be framed as a shift from
only winter heating adequacy toward a combined heating, cooling-comfort, and
flexibility problem.

Cooling boundary:

The model does not report cooling final energy or cooling electricity demand.
Cooling-related values are climate and comfort-pressure indicators only. They
support discussion of reversible heat pumps and adaptation value, but they must
not be interpreted as active cooling consumption.

External motivation:

The European Environment Agency's sustainable cooling briefing notes that rising
temperatures, ageing, and urbanisation increase vulnerability to heat and can
drive growth in inefficient active cooling if no action is taken. This supports
the relevance of cooling and adaptation discussion, while the thesis results
remain based on the model's own climate and comfort indicators.

Source:
`https://www.eea.europa.eu/en/analysis/publications/cooling-buildings-sustainably-in-europe-exploring-the-links-between-climate-change-mitigation-and-adaptation-and-their-social-impacts/`
