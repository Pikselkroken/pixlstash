/**
 * Route names that more than one place has to agree on.
 *
 * Its own module, and deliberately free of imports: `router/index.js` calls
 * `createRouter`, so a component importing a constant from it drags the whole
 * router construction into that component's module graph - and into every test
 * that mocks `vue-router` without providing `createRouter`.
 */

/**
 * Every route the model shelf answers to.
 *
 * TWO places decide things from this - `useAppNavigation`, which decides
 * whether the shelf is showing, and `SideBar`, which decides whether its Models
 * entry is the current page and whether a picture selection may light a row of
 * its own. They were separate literals, and adding the runs tab broke the
 * sidebar half silently: on `/models/runs` the Models entry went dark AND the
 * underlying selection lit a second destination, which is the exact
 * two-active-destinations defect the sidebar guard exists to prevent.
 *
 * The two predicates built from this list are deliberately not identical.
 * `useAppNavigation` additionally requires `!isReadOnly`, because its predicate
 * decides whether `App.vue` MOUNTS the shelf, and a READ session must never
 * mount it (#1014). `SideBar` asks the narrower question - is this a shelf
 * route - which is what `aria-current` and `selectionOwnsHighlight` want. The
 * route list is the part that has to agree, and it is the part this constant
 * holds.
 */
export const MODEL_SHELF_ROUTES = ["models", "models-runs"];
