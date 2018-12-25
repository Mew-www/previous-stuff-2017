"""
reference:
https://docs.djangoproject.com/en/1.11/ref/models/fields/
"""
from django.db import models
import json
from .app_lib import truncate_coordinate_to_8_decimal_float


class ThirdPartyProvider(models.Model):
    name = models.CharField(max_length=255)


class Route(models.Model):
    # If route from a third party API -> reference to their external system and the ID it uses here
    external_system = models.ForeignKey('ThirdPartyProvider', on_delete=models.CASCADE)
    external_id = models.CharField(max_length=255, null=True)

    # If route either is self-made, or created-as-modified-version-of-existing-route, save user's name from FB/G-Mail
    creator = models.CharField(max_length=255, null=True)

    # If user created the route, it is possible to be set "private", otherwise it is locked as "public"
    is_public = models.BooleanField()

    # Bounding box / "borders of route" expressed as top-left & bottom-right coordinates
    bounding_box_larger_edge_lat = models.FloatField()  # No DecimalField(max_digits=18, decimal_places=15), just no
    bounding_box_larger_edge_lng = models.FloatField()
    bounding_box_lesser_edge_lat = models.FloatField()
    bounding_box_lesser_edge_lng = models.FloatField()

    # Generic info of the route
    distance = models.IntegerField()
    accumulated_elevation_gain = models.IntegerField(null=True)
    accumulated_elevation_loss = models.IntegerField(null=True)

    # Starting point
    first_lat = models.FloatField()
    first_lng = models.FloatField()

    # Finish point
    last_lat = models.FloatField()
    last_lng = models.FloatField()

    # Route point-by-point
    track_points = models.TextField()  # JSON text [{d: .., x: .., y:..}, {d:.., x: .., y:.., e:..}, ...]

    def save(self, *args, **kwargs):
        self.bounding_box_larger_edge_lat = truncate_coordinate_to_8_decimal_float(self.bounding_box_larger_edge_lat)
        self.bounding_box_larger_edge_lng = truncate_coordinate_to_8_decimal_float(self.bounding_box_larger_edge_lng)
        self.bounding_box_lesser_edge_lat = truncate_coordinate_to_8_decimal_float(self.bounding_box_lesser_edge_lat)
        self.bounding_box_lesser_edge_lng = truncate_coordinate_to_8_decimal_float(self.bounding_box_lesser_edge_lng)
        self.distance = int(round(self.distance))
        if self.accumulated_elevation_gain:
            self.accumulated_elevation_gain = int(round(self.accumulated_elevation_gain))
        if self.accumulated_elevation_loss:
            self.accumulated_elevation_loss = int(round(self.accumulated_elevation_loss))
        self.first_lat = truncate_coordinate_to_8_decimal_float(self.first_lat)
        self.first_lng = truncate_coordinate_to_8_decimal_float(self.first_lng)
        self.last_lat = truncate_coordinate_to_8_decimal_float(self.last_lat)
        self.last_lng = truncate_coordinate_to_8_decimal_float(self.last_lng)
        super(Route, self).save(*args, **kwargs)

    def to_dict(self):
        return {
            "id":                           self.id,
            "external_system":              self.external_system.name,
            "external_id":                  self.external_id,
            "creator":                      self.creator,
            "is_public":                    self.is_public,
            "bounding_box_larger_edge_lat": self.bounding_box_larger_edge_lat,
            "bounding_box_larger_edge_lng": self.bounding_box_larger_edge_lng,
            "bounding_box_lesser_edge_lat": self.bounding_box_lesser_edge_lat,
            "bounding_box_lesser_edge_lng": self.bounding_box_lesser_edge_lng,
            "distance":                     self.distance,
            "accumulated_elevation_gain":   self.accumulated_elevation_gain,
            "accumulated_elevation_loss":   self.accumulated_elevation_loss,
            "first_lat":                    self.first_lat,
            "first_lng":                    self.first_lng,
            "last_lat":                     self.last_lat,
            "last_lng":                     self.last_lng,
            "track_points":                 json.loads(self.track_points)
        }


class Comment(models.Model):
    content = models.TextField()
    lat = models.FloatField(null=True)
    lng = models.FloatField(null=True)
    route = models.ForeignKey('Route', on_delete=models.CASCADE)

    def save(self, *args, **kwargs):
        if self.lat:
            self.lat = truncate_coordinate_to_8_decimal_float(self.lat)
        if self.lng:
            self.lng = truncate_coordinate_to_8_decimal_float(self.lng)
        super(Comment, self).save(*args, **kwargs)
