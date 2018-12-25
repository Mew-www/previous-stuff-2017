from django.conf import settings
from .app_lib import calculate_wgs84_lat_lon_by_offset
import psycopg2


def get_nearest_point_lat_lon_id(lat, lon):

    database_query = ("""
        SELECT y1, x1, source
          FROM fi_2po_4pgr 
          ORDER BY 
            ST_Distance(
              geom_way, 
              ST_SetSRID(ST_MakePoint(%s,%s), 4326) -- SRID: WGS84
            )
          LIMIT 1;
        """)
    query_params = (lon, lat)

    # Create db connection
    data_source_name = "host={} dbname={} user={} password={}".format(
        settings.GISDB_HOST,
        settings.GISDB_NAME,
        settings.GISDB_USER,
        settings.GISDB_PASSWORD
    )
    dbh = psycopg2.connect(data_source_name)

    # Execute query
    cursor = dbh.cursor()
    cursor.execute(database_query, query_params)
    result_tuple = cursor.fetchone()

    # Close db connection
    cursor.close()
    dbh.close()

    # Process result
    if not result_tuple or not (result_tuple[0] and result_tuple[1]):
        return None
    else:
        way_source_lat, way_source_lon, way_source_id = map(lambda v: float(v), result_tuple)
        return way_source_lat, way_source_lon, way_source_id


def get_points_lat_lon_id_distance_per_sector_within_range(center_lat,
                                                           center_lon,
                                                           optimal_range_km,
                                                           lowest_permitted_range_km,
                                                           highest_permitted_range_km):

    # Calculate bbox
    extent_m, n_extent_m = highest_permitted_range_km*1000*2, (-1)*highest_permitted_range_km*1000*2
    extent_high_lat, extent_high_lon = calculate_wgs84_lat_lon_by_offset(center_lat, center_lon, extent_m, extent_m)
    extent_low_lat, extent_low_lon = calculate_wgs84_lat_lon_by_offset(center_lat, center_lon, n_extent_m, n_extent_m)

    # Prepare DB query
    database_query = ("""
        SELECT 
        y1 as lat, 
        x1 as lon, 
        source as source,
        ST_Distance_Sphere(ST_SetSRID(ST_MakePoint(%s,%s), 4326), geom_way) as metres_away,
        clazz
          FROM fi_2po_4pgr
          WHERE
            geom_way && ST_MakeEnvelope(%s,%s,%s,%s,4326)
            AND ST_Distance_Sphere(
              ST_SetSRID(ST_MakePoint(%s,%s), 4326), -- SRID: WGS84
              geom_way
            ) > %s -- "minimum metres away from center"
            AND ST_Distance_Sphere(
              ST_SetSRID(ST_MakePoint(%s,%s), 4326), -- SRID: WGS84
              geom_way
            ) < %s -- "maximum metres away from center"
          ORDER BY
            ABS(
              ST_Distance_Sphere(
                ST_SetSRID(ST_MakePoint(%s,%s), 4326), -- SRID: WGS84
                geom_way
              )
              - %s
            )
            ASC; -- "ABSolute distance nearest to center"
        """)
    query_params = (
        center_lon, center_lat,
        extent_high_lon, extent_high_lat, extent_low_lon, extent_low_lat,
        center_lon, center_lat,
        int(lowest_permitted_range_km * 1000),
        center_lon, center_lat,
        int(highest_permitted_range_km * 1000),
        center_lon, center_lat,
        int(optimal_range_km * 1000)
    )

    # Create db connection
    data_source_name = "host={} dbname={} user={} password={}".format(
        settings.GISDB_HOST,
        settings.GISDB_NAME,
        settings.GISDB_USER,
        settings.GISDB_PASSWORD
    )
    dbh = psycopg2.connect(data_source_name)

    # Execute query
    cursor = dbh.cursor()
    cursor.execute(database_query, query_params)
    sectors = {
        "NE": None,
        "SE": None,
        "SW": None,
        "NW": None
    }
    MISC_OTHER_WAY_TYPE = 69
    way_priority = [31, 81, MISC_OTHER_WAY_TYPE]  # 31 -> tertiary, 81 -> cycleway ; ref: osm2po.config
    result_tuple = cursor.fetchone()
    while result_tuple:
        # End condition (all sectors filled with nearest match && optimal type), early return
        if (
                    (sectors['NE'] and sectors['NE']['way_type'] == way_priority[0])
                and (sectors['SE'] and sectors['SE']['way_type'] == way_priority[0])
                and (sectors['SW'] and sectors['SW']['way_type'] == way_priority[0])
                and (sectors['NW'] and sectors['NW']['way_type'] == way_priority[0])):
            break

        # Unpack the (single) result
        lat, lon, way_source, distance, way_type = map(lambda v: float(v), result_tuple)
        if way_type not in way_priority or not way_type:
            way_type = MISC_OTHER_WAY_TYPE

        # Find what sector the result belongs to, and if empty then add it
        if lon > center_lon:
            # Then it's to east
            if lat > center_lat:
                # then it's to north-east
                if not sectors['NE'] or way_priority.index(way_type) < way_priority.index(sectors['NE']['way_type']):
                    sectors['NE'] = {
                        'lat': lat,
                        'lon': lon,
                        'node_id': way_source,
                        'distance': distance,
                        'way_type': way_type
                    }
            else:
                # it's to south-east
                if not sectors['SE'] or way_priority.index(way_type) < way_priority.index(sectors['SE']['way_type']):
                    sectors['SE'] = {
                        'lat': lat,
                        'lon': lon,
                        'node_id': way_source,
                        'distance': distance,
                        'way_type': way_type
                    }
        else:
            # it's to west
            if lat > center_lat:
                # then it's to north-west
                if not sectors['NW'] or way_priority.index(way_type) < way_priority.index(sectors['NW']['way_type']):
                    sectors['NW'] = {
                        'lat': lat,
                        'lon': lon,
                        'node_id': way_source,
                        'distance': distance,
                        'way_type': way_type
                    }
            else:
                # it's to south-west
                if not sectors['SW'] or way_priority.index(way_type) < way_priority.index(sectors['SW']['way_type']):
                    sectors['SW'] = {
                        'lat': lat,
                        'lon': lon,
                        'node_id': way_source,
                        'distance': distance,
                        'way_type': way_type
                    }

        # Continue iterating results (end condition in beginning of loop)
        result_tuple = cursor.fetchone()

    # Close db connection
    cursor.close()
    dbh.close()

    return sectors
