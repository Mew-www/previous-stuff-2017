from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError
from ..app_lib import calculate_wgs84_lat_lon_by_offset, calculate_wgs84_lat_lon_by_degree_and_distance
from ..app_lib_sql import get_nearest_point_lat_lon_id, get_points_lat_lon_id_distance_per_sector_within_range
import re
import psycopg2
import json
import math


DISTANCE_MARGIN = 0.25
DISPOSE_MARGIN = 0.4


def generate_ksp_path(request):
    # Gather arguments
    input_start = request.GET.get('start_coordinates', None)
    distance_km = request.GET.get('distance_km', None)

    # Validate arguments
    if input_start is None or ',' not in input_start:
        return HttpResponseBadRequest('Invalid GET param "start_coordinates". Should be "lat,lon" (without quotes).')
    elif not len(input_start.split(',')) == 2:
        return HttpResponseBadRequest('Invalid coordinate(s) in GET param "start_coordinates". Provide both "lat,lon"')
    elif not all(re.compile(r'^\d+([.]\d+)?$').match(c) for c in input_start.split(',')):
        return HttpResponseBadRequest('Invalid coordinate(s) in GET param "start_coordinates". Format: "nn.mm,nn.mm"')
    if distance_km is None:
        return HttpResponseBadRequest('Invalid GET param "distance_km".')
    elif not re.compile(r'^\d+([.]\d+)?$').match(distance_km):
        return HttpResponseBadRequest('Invalid GET param "distance_km" e.g. "1", "12.3", "99"')

    # Transform value arguments
    start_lat, start_lon = map(lambda coord: float(coord), input_start.split(','))
    distance_km = float(distance_km)

    # Query database for actual start point (nearest node)
    start_point_lat, start_point_lon, start_point_id = get_nearest_point_lat_lon_id(start_lat, start_lon)

    # Guesstimate city routes as rectangular -> calculate route length based on trigonometry
    def calc_round_trip_point_range(route_distance):
        one_square_edge_length = route_distance/4
        return math.sqrt(math.pow(one_square_edge_length,2)+math.pow(one_square_edge_length, 2))
    round_trip_point_optimal_range = calc_round_trip_point_range(distance_km)
    round_trip_point_minimum_range = calc_round_trip_point_range(distance_km * (1 - DISTANCE_MARGIN))
    round_trip_point_maximum_range = calc_round_trip_point_range(distance_km * (1 + DISTANCE_MARGIN))

    # Query database destination/round trip points per sector
    sectors = get_points_lat_lon_id_distance_per_sector_within_range(
        start_point_lat,
        start_point_lon,
        round_trip_point_optimal_range,
        round_trip_point_minimum_range,
        round_trip_point_maximum_range
    )

    # Construct extent / bbox
    extent_m, n_extent_m = (distance_km*(1+DISTANCE_MARGIN)*1000)+1000, ((-1)*distance_km*(1+DISTANCE_MARGIN)*1000)-1000
    extent_high_lat, extent_high_lon = calculate_wgs84_lat_lon_by_offset(start_lat, start_lon, extent_m, extent_m)
    extent_low_lat, extent_low_lon = calculate_wgs84_lat_lon_by_offset(start_lat, start_lon, n_extent_m, n_extent_m)

    known_predefined_sectors = ['NE', 'SE', 'SW', 'NW']
    available_sectors = filter(lambda sector_key: sectors[sector_key] is not None, known_predefined_sectors)
    final_routes = []
    for available_key in available_sectors:

        # Create SQL queries
        temp_table_query = """
            CREATE 
                TEMP TABLE temp_ksp_result
                    ON COMMIT 
                        DROP
                AS SELECT 
                seq,
                id1 as route,
                id2 as from_node,
                id3 as edge,
                geom_way,
                result.cost as seq_cost, -- currently we calculate cost from geography but could compare to this
                -- currently un-used properties of edge below
                id, osm_id, osm_name, osm_meta, osm_source_id, osm_target_id, clazz, flags, source, target, km,
                kmh, s.cost as cost, reverse_cost, x1, y1, x2, y2
                    FROM PGR_KSP(
                        'SELECT 
                         s.id as id, 
                         s.source as source, 
                         s.target as target, 
                         ST_Length(s.geom_way::geography) as cost
                             FROM fi_2po_4pgr as s
                             WHERE s.geom_way && ST_MakeEnvelope(%s, %s, %s, %s, 4326) -- within bbox
                         ',
                        %s, -- start location ("way -> source (-> id)")
                        %s, -- end location ("round trip" -spot)
                        3,
                        false
                    )
                    AS result
                        -- join K-S-P's result set with main table -> to retrieve (ways') geometry
                        INNER JOIN fi_2po_4pgr AS s ON result.id3 = s.id;
        """
        temp_table_query_params = (
            extent_high_lon, extent_high_lat, extent_low_lon, extent_low_lat,
            int(start_point_id),
            int(sectors[available_key]['node_id'])
        )

        ten_routes_query = """
            SELECT seq, route, edge, geom_way FROM temp_ksp_result ORDER BY seq ASC;
        """

        # Create db connection
        data_source_name = "host={} dbname={} user={} password={}".format(
            settings.GISDB_HOST,
            settings.GISDB_NAME,
            settings.GISDB_USER,
            settings.GISDB_PASSWORD
        )
        dbh = psycopg2.connect(data_source_name)

        # Execute queries
        cursor = dbh.cursor()
        cursor.execute(temp_table_query, temp_table_query_params)
        cursor.execute(ten_routes_query)

        # Gather query results to aggregate sequences/segments
        routes = []
        edge_repetitions = {}
        result_tuple = cursor.fetchone()
        current_route, route_start_sequence = None, None
        while result_tuple:
            seq, route_id, edge, geom_way = result_tuple
            seq = int(seq)
            route_id = int(route_id)
            # Check start of new path
            if current_route != route_id:
                current_route = route_id
                route_start_sequence = seq  # Mark start of new path and use it to have all sequences 0 .. M
            # Segments come in order of route ids: 0, 1, 2, ...; Ensure we have an array for this route
            if len(routes) < (route_id+1):
                routes.append([])
            # Add segment data
            routes[route_id].append({
                # Ensure all sequences start from 0 per each route
                'seq': seq - route_start_sequence,
                'edge': edge
            })
            # Add to repetitions per edge counter
            if edge not in edge_repetitions:
                edge_repetitions[edge] = 0
            edge_repetitions[edge] += 1
            # Continue iterating results
            result_tuple = cursor.fetchone()

        # IF NO ROUTES, then skip sector
        if len(routes) == 0:
            sectors[available_key]['route'] = "No routes at all for sector {}".format(available_key)
            continue

        # Calculate edges' mid-distance weights (based on "how close to middle sequence is")
        all_mid_distance_edge_weights = {}
        for r in routes:
            total_sequences_in_route = len(r)
            for segment in r:
                edge_id = segment['edge']
                if edge_id not in all_mid_distance_edge_weights:
                    all_mid_distance_edge_weights[edge_id] = []
                # total_sequences/2 is middle point
                # abs(middle_point - seq) is "how off middle point this is"
                # / middle point gives the offset as a 1..0..1 ratio
                # we reduce this of 1, to have it around as 0..1..0 instead
                weight = 1 - (abs((total_sequences_in_route/2) - segment['seq']) / (total_sequences_in_route/2))
                all_mid_distance_edge_weights[edge_id].append(weight)

        # Calculate mid-distance weight averages per each edge
        average_mid_distance_edge_weights = {
            edge_id: sum(all_weights)/float(len(all_weights))
            for edge_id, all_weights
            in all_mid_distance_edge_weights.items()
        }

        # Calculate repetition weights per each edge (based on "how many times of all routes, edge was crossed"
        repetition_edge_weights = {
            edge_id: counts_crossed/len(routes)
            for edge_id, counts_crossed
            in edge_repetitions.items()
        }

        # Apply mid-distance-ratio averages to ratio-times-crossed, to have a "dispose or not" value between 0 and 1
        dispose_values_per_edge = [
            {'edge_id': edge_id, 'value': repetition_ratio * average_mid_distance_edge_weights[edge_id]}
            for edge_id, repetition_ratio
            in repetition_edge_weights.items()
        ]

        # (Less aggressive disposing for more options)
        # Restrict disposed edges to those found in first path
        edge_ids_in_first_path = list(map(lambda seg: seg['edge'], routes[0]))
        limited_dispose_values_per_edge = [
            edge
            for edge
            in dispose_values_per_edge
            if edge['edge_id'] in edge_ids_in_first_path
        ]

        disposed_edges_strings = list(map(
            lambda e: str(e['edge_id']),
            filter(
                lambda e: e['value'] > DISPOSE_MARGIN,
                limited_dispose_values_per_edge
            )
        ))

        # IF NOTHING DISPOSED, then skip sector
        if len(disposed_edges_strings) == 0:
            sectors[available_key]['route'] = "No paths disposed in sector {}".format(available_key)
            continue

        # Create temp table also for the alternative ("secondary") route, as we first check if it's valid, then UNION
        alt_temp_table_query = """
            CREATE 
                TEMP TABLE temp_alternative_ksp_result
                    ON COMMIT 
                        DROP
                AS SELECT 
                seq,
                id1 as route,
                id2 as from_node,
                id3 as edge,
                geom_way,
                result.cost as seq_cost, -- currently we calculate cost from geography but could compare to this
                -- currently un-used properties of edge below
                id, osm_id, osm_name, osm_meta, osm_source_id, osm_target_id, clazz, flags, source, target, km,
                kmh, s.cost as cost, reverse_cost, x1, y1, x2, y2
                    FROM PGR_KSP(
                        'SELECT 
                         s.id as id, 
                         s.source as source, 
                         s.target as target, 
                         ST_Length(s.geom_way::geography) as cost
                             FROM fi_2po_4pgr as s
                             WHERE s.geom_way && ST_MakeEnvelope(%s, %s, %s, %s, 4326) -- within bbox
                                AND id NOT IN (""" + ','.join(disposed_edges_strings) + """)
                         ',
                        %s, -- start location ("way -> source (-> id)")
                        %s, -- end location ("round trip" -spot)
                        1,
                        false
                    )
                    AS result
                        -- join K-S-P's result set with main table -> to retrieve (ways') geometry
                        INNER JOIN fi_2po_4pgr AS s ON result.id3 = s.id;
        """
        alt_temp_table_query_params = (
            extent_high_lon, extent_high_lat, extent_low_lon, extent_low_lat,
            int(start_point_id),
            int(sectors[available_key]['node_id'])
        )

        one_alt_route_query = """
            SELECT 
            ST_AsGeoJSON(ST_UNION(geom_way)) as geojson,
            ST_Length(ST_UNION(geom_way)::geography) as length 
                FROM temp_alternative_ksp_result;
        """

        cursor.execute(alt_temp_table_query, alt_temp_table_query_params)
        cursor.execute(one_alt_route_query)

        result_tuple = cursor.fetchone()
        geojson = None if (not result_tuple or not result_tuple[0]) else json.loads(result_tuple[0])
        length = None if (not result_tuple or not result_tuple[1]) else result_tuple[1]

        # IF NO ALT ROUTE, then skip sector
        if not geojson:
            sectors[available_key]['route'] = "Couldn't create alt route"
            continue

        # Merge routes using ST_UNION
        first_and_alt_route_query = """
            SELECT 
            ST_AsGeoJSON(ST_UNION(both_routes.geom_way)) as geojson, 
            ST_Length(ST_UNION(both_routes.geom_way)::geography) as length
            FROM
            (
                SELECT geom_way 
                    FROM temp_ksp_result 
                    WHERE route=0
                UNION
                SELECT geom_way
                    FROM temp_alternative_ksp_result
                    -- only 1 alt route fetched so no need a where -clause
            ) AS both_routes;
        """

        cursor.execute(first_and_alt_route_query)

        result_tuple = cursor.fetchone()
        geojson = None if (not result_tuple or not result_tuple[0]) else json.loads(result_tuple[0])
        length = None if (not result_tuple or not result_tuple[1]) else result_tuple[1]

        # Close db connection
        cursor.close()
        dbh.commit()
        dbh.close()

        if geojson:
            route_obj = {
                'geojson': geojson,
                'length_m': length,
                'sector': available_key
            }
            sectors[available_key]['route'] = route_obj
            final_routes.append(route_obj)

    # Process result
    if len(final_routes) == 0:
        return HttpResponseNotFound('No path available, try with other arguments.')
    else:
        closest_route = min(
            [route for route in final_routes],
            key=lambda route: abs((distance_km*1000) - route['length_m'])
        )
        closest_route['length_m'] = int(round(closest_route['length_m']))
        return HttpResponse(json.dumps(closest_route), content_type='application/json')
