// Deployment-specific viewer settings.
//
// Intentionally left blank in the repository: the values below name and locate
// the sites this instance covers, which is not information the repo carries.
// Fill them in at deploy time, alongside `data/groves.geojson`.
//
// Every field is optional. Left empty, the viewer falls back to behaviour that
// reveals nothing: it fits the map to whatever imagery is actually present and
// selects no grove.
const VIEWER_CONFIG = {
  // Grove to select on load, matched against a feature name in
  // data/groves.geojson. Empty -> no grove selected, map fits to the data.
  homeGrove: '',

  // Initial map centre as [lon, lat]. Null -> fit to the loaded captures.
  mapCenter: null,

  // Initial zoom. Ignored when mapCenter is null.
  mapZoom: 15,

  // Example shown in the "Go to Coordinates" box, as 'lat, lng'.
  // Empty -> a generic hint.
  coordExample: '',

  // Display names for the model's species classes. The keys are fixed by the
  // PMTiles schema; only the labels change per deployment. Omitted keys fall
  // back to the generic defaults shown here.
  //
  // `seqmat` also appears lowercased inside collection preset names, as in
  // "Dead <seqmat> trees" — phrase it as a noun phrase that reads well there.
  speciesLabels: {
    seqmat: 'Mature',
    seqsp: 'Sapling',
    tree: 'Other',
  },
};
