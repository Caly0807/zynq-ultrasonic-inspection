#include "ut_map.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

int main(int argument_count, char **arguments)
{
    static ut_map_t map;
    char error[160];
    const char *valid = "UTMAP,1,2,1,10,5,30,5,DEMO,MANUAL_UNVERIFIED\nCELL,0,0,SUSPECT,1,12\nCELL,1,0,UNSCANNED,0,\n";
    assert(ut_map_decode(valid, strlen(valid), &map, error, sizeof(error)));
    assert(map.columns == 2 && map.rows == 1 && map.suspect_cells == 1 && map.sampled_cells == 1);
    assert(map.cells[0].depth_valid && map.cells[0].depth_mm == 12);
    assert(ut_map_validate(&map));
    ut_map_t candidate = map;
    candidate.length_mm = NAN;
    assert(!ut_map_validate(&candidate));
    candidate = map; candidate.grid_mm = 0;
    assert(!ut_map_validate(&candidate));
    candidate = map; candidate.cells[0].depth_mm = INFINITY;
    assert(!ut_map_validate(&candidate));
    candidate = map; candidate.cells[0].status = UT_MAP_NO_INDICATION;
    assert(!ut_map_validate(&candidate));
    candidate = map; candidate.sampled_cells++;
    assert(!ut_map_validate(&candidate));
    candidate = map; memset(candidate.source, 'X', sizeof(candidate.source));
    assert(!ut_map_validate(&candidate));
    const char *invalid[] = {
        "UTMAP,1,2,1,10,5,30,5,DEMO,MANUAL_UNVERIFIED\nCELL,0,0,SUSPECT,1,12\n",
        "UTMAP,1,1,1,5,5,30,5,LIVE,MANUAL_UNVERIFIED\nCELL,0,0,SUSPECT,1,nan\n",
        "UTMAP,1,1,1,5,5,30,5,LIVE,MANUAL_UNVERIFIED\nCELL,0,0,NO_INDICATION,1,12\n",
        "UTMAP,1,1,1,5,5,30,5,LIVE,MANUAL_UNVERIFIED\nCELL,0,0,SUSPECT,1,31\n",
        "UTMAP,1,1,1,5,5,30,5,LIVE,AUTO_CALIBRATED\nCELL,0,0,SUSPECT,1,12\n",
        "UTMAP,1,2,1,10,5,30,5,DEMO,MANUAL_UNVERIFIED\nCELL,0,0,SUSPECT,1,12\nCELL,0,0,SUSPECT,1,12\n",
        "UTMAP,1,1,1,5,5,30,5,DEMO,MANUAL_UNVERIFIED\nCELL,0,0,UNSCANNED,1,\n"
    };
    for (unsigned index = 0; index < sizeof(invalid) / sizeof(invalid[0]); index++) {
        assert(!ut_map_decode(invalid[index], strlen(invalid[index]), &map, error, sizeof(error)));
        assert(map.columns == 2 && map.cells[0].depth_valid);
    }
    if (argument_count > 1) {
        assert(ut_map_load_csv(arguments[1], &map, error, sizeof(error)));
        assert(map.point_count > 0 && map.suspect_cells > 0 && !strcmp(map.source, "DEMO"));
        printf("PYTHON_C_BRIDGE_PASS cells=%u sampled=%u suspects=%u depth=%u source=%s\n", map.rows * map.columns, map.sampled_cells, map.suspect_cells, map.depth_cells, map.source);
    }
    puts("UTMAP C validation passed");
    return 0;
}
