from django import template


register = template.Library()


@register.filter
def mapping_value(mapping, key):
    if not mapping:
        return None
    return mapping.get(key)
