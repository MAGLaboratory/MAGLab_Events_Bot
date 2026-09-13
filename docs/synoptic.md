# Synoptic View

## Upstream sources

The deployed HAL page and its source repository use the same frontend updater and server-side
conditionals:

- Frontend updater: [`sensor_updater.js`](https://github.com/MAGLaboratory/website/blob/b4bc350bdfc250845d054d6595e1a31a864b52ba/home/public/js/sensor_updater.js)
- Backend sensor normalization: [`hal_helpers_old.php`](https://github.com/MAGLaboratory/website/blob/b4bc350bdfc250845d054d6595e1a31a864b52ba/home/protected/maglab/app/helpers/hal_helpers_old.php)
- Server-rendered SVG: [`_synoptic.php.haml`](https://github.com/MAGLaboratory/website/blob/b4bc350bdfc250845d054d6595e1a31a864b52ba/src/views/hal/_synoptic.php.haml)
- HAL page controller: [`Status.php`](https://github.com/MAGLaboratory/website/blob/b4bc350bdfc250845d054d6595e1a31a864b52ba/home/protected/maglab/app/controllers/Hal/Status.php)

The website repository's latest commit at the time of review was
`b4bc350bdfc250845d054d6595e1a31a864b52ba` from September 16, 2025. The latest
condition-related changes were June 26, 2025 for the updater, March 9, 2024 for backend sensor
normalization, and January 27, 2021 for the SVG template.

The newer [HalReporter](https://github.com/MAGLaboratory/HalReporter/tree/b5876f498a3aabe0e19f04cbd7a63fc9e2998992)
collects MQTT checkups and forwards them to the website. The
[haldor](https://github.com/MAGLaboratory/haldor/tree/cef9e367ea1865dd8b385afddabd1f561d2bb014)
collector defines the physical GPIO and temperature sensor names. Neither repository overrides
the display conditions below.

## Mirrored conditions

| Component | Condition |
| --- | --- |
| Whole view | Technical failure when the newest sensor update is older than 15 minutes |
| Space status | Open only when `Open Switch` is `1` and recent; otherwise closed |
| Privacy | `Privacy_Switch = 1` masks door and motion activity |
| Doors | Open when the binary field is `1`; closed when `0` |
| Motion | Active when the binary field is `1` and its sample is no more than 20 minutes old |
| Temperatures | Grafana millidegrees Celsius divided by 1000, converted to Fahrenheit, and rounded to whole degrees |
| Failure rendering | Unknown status, red failure marks, and `XX°C / XX°F` temperatures |

The bot reads all required fields with one Grafana datasource-proxy request. The SVG geometry is
stored locally in `data/static/maglab_synoptic_template.svg`, and Python applies the same
conditions before rasterizing it. The output is 800×320 pixels (a 5:2 Discord event-banner ratio)
with a view box selected to retain the full floor plan without stretching it.

## Discord cropping

Discord's API documents the scheduled-event image field and supported image formats, but not a
fixed display crop. The expanded event view displays nearly all of the 800×320 image, while the
Events list currently uses a shallow, center-aligned crop. Measurements from the desktop client
put that list viewport at approximately 4.6:1, which retains about image rows 73 through 247 from
an 800×320 source.

The compact space, front-door, and pod-bay-door text panel therefore sits wholly inside that
center safe area and follows the original floorplan's room-label styling. It also shows the newest
Grafana sample timestamp in MAGLab local time. Temperature labels use SVG paths rather than emoji
for their thermometer symbols, so they do not depend on the fonts installed on the bot host.
