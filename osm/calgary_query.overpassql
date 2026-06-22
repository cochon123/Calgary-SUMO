[out:xml][timeout:300];
// City of Calgary municipal boundary (Alberta, Canada)
area["name"="Calgary"]["boundary"="administrative"]["admin_level"="8"]->.a;
(
  way["highway"](area.a);
  way["railway"](area.a);
);
out body;
>;
out skel qt;
