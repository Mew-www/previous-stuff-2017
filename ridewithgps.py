from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse, HttpResponseBadRequest
import requests
import json
import urllib.parse

from ..models import Route, ThirdPartyProvider
from ..app_lib import RoutePreview


def get_authtoken():
    r = requests.get('https://ridewithgps.com/users/current.json'
                     + '?email=' + settings.RIDEWITHGPS_EMAIL
                     + '&password=' + settings.RIDEWITHGPS_PASSWORD
                     + '&apikey=' + settings.RIDEWITHGPS_APIKEY)
    user_obj = json.loads(r.text)
    return user_obj['user']['auth_token']


# Returns {'results': <list-serialized-using-RoutePreview.to_dict()>, 'results_count': <int>}
def search_routes(request):
    querystring = urllib.parse.urlencode({
        "apikey": settings.RIDEWITHGPS_APIKEY,
        "version": settings.RIDEWITHGPS_APIVERSION,
        "auth_token": get_authtoken(),
        "search[keywords]":       request.GET.get('keywords', ''),
        "search[start_location]": request.GET.get('start_location', ''),
        "search[start_distance]": request.GET.get('start_distance', ''),
        "search[elevation_max]":  request.GET.get('elevation_max', ''),
        "search[elevation_min]":  request.GET.get('elevation_min', ''),
        "search[length_max]":     request.GET.get('length_max', ''),
        "search[length_min]":     request.GET.get('length_min', ''),
        "search[offset]":         request.GET.get('offset', ''),
        "search[limit]":          request.GET.get('limit', ''),
        "search[sort_by]":        request.GET.get('sort_by', '')
    })
    r = requests.get('https://ridewithgps.com/find/search.json?%s' % querystring)
    response_obj = json.loads(r.text)
    # filter away "segment"s and whatnot possibly other
    trips_and_routes = list(filter(lambda x: x['type'] == "route" or x['type'] == "trip", response_obj['results']))
    # validate and serialize valid routes
    valid_routes = []
    for raw_route_dataset in trips_and_routes:
        try:
            route_preview = RoutePreview('RIDEWITHGPS', raw_route_dataset)
            valid_routes.append(route_preview.to_dict())
        except ValueError:
            pass
    return HttpResponse(json.dumps({
        'results': valid_routes,
        'results_count': response_obj['results_count']
    }, indent=4), content_type='application/json')


# Returns Route.to_dict() as 'application/json'
def get_route(request, objtype, identifier):
    endpoint = "trips" if objtype == "trip" else "routes"
    querystring = urllib.parse.urlencode({
        "apikey": settings.RIDEWITHGPS_APIKEY,
        "version": settings.RIDEWITHGPS_APIVERSION,
        "auth_token": get_authtoken()
    })
    r = requests.get('https://ridewithgps.com/%s/%s.json?%s' % (endpoint, identifier, querystring))
    response_obj = json.loads(r.text)
    # Ensure existence of the ThirdPartyProvider
    try:
        third_party_provider = ThirdPartyProvider.objects.get(name='RIDEWITHGPS')
    except ObjectDoesNotExist:
        third_party_provider = ThirdPartyProvider(name='RIDEWITHGPS')
        third_party_provider.save()
    # Search if this route already exists in database, else create
    typekey = response_obj['type']  # Either "route" or "trip"
    external_id = "%s:%s" % (typekey, response_obj[typekey]['id'])
    try:
        the_route = Route.objects.get(external_system=third_party_provider, external_id=external_id)
    except ObjectDoesNotExist:
        bounding_box_ne = max(response_obj[typekey]['bounding_box'], key=lambda x: x['lat'])
        bounding_box_sw = min(response_obj[typekey]['bounding_box'], key=lambda x: x['lat'])
        try:
            the_route = Route(
                external_system=third_party_provider,
                external_id=external_id,
                is_public=True,
                bounding_box_larger_edge_lat=bounding_box_ne['lat'],
                bounding_box_larger_edge_lng=bounding_box_ne['lng'],
                bounding_box_lesser_edge_lat=bounding_box_sw['lat'],
                bounding_box_lesser_edge_lng=bounding_box_sw['lng'],
                distance=response_obj[typekey]['distance'],
                accumulated_elevation_gain=response_obj[typekey]['elevation_gain'],
                accumulated_elevation_loss=response_obj[typekey]['elevation_loss'],
                first_lat=response_obj[typekey]['first_lat'],
                first_lng=response_obj[typekey]['first_lng'],
                last_lat=response_obj[typekey]['last_lat'],
                last_lng=response_obj[typekey]['last_lng'],
                track_points=json.dumps(response_obj[typekey]['track_points'])  # Saved TextField
            )
        except ValueError:
            return HttpResponseBadRequest('Tried to query an invalid route. (Where did frontend get this ID?)')
        the_route.save()
    return HttpResponse(json.dumps(the_route.to_dict()), content_type='application/json')
