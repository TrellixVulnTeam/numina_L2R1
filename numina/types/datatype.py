#
# Copyright 2008-2018 Universidad Complutense de Madrid
#
# This file is part of Numina
#
# SPDX-License-Identifier: GPL-3.0+
# License-Filename: LICENSE.txt
#

import inspect
import collections

from numina.exceptions import ValidationError
from numina.exceptions import NoResultFound
from .base import DataTypeBase
from .typedialect import dialect_info


class DataType(DataTypeBase):
    """Base class for input/output types of recipes.

    """
    def __init__(self, ptype, default=None, **kwds):
        super(DataType, self).__init__(**kwds)
        self.internal_type = ptype
        self.internal_dialect = dialect_info(self)
        self.internal_default = default

    def convert(self, obj):
        """Basic conversion to internal type

        This method is intended to be redefined by subclasses
        """
        return obj

    def convert_in(self, obj):
        """Basic conversion to internal type of inputs.

        This method is intended to be redefined by subclasses
        """
        return self.convert(obj)

    def convert_out(self, obj):
        """Basic conversion to internal type of outputs.

        This method is intended to be redefined by subclasses
        """
        return self.convert(obj)

    def validate(self, obj):
        """Validate convertibility to internal representation

        Returns
        -------
        bool
          True if 'obj' matches the data type

        Raises
        -------
        ValidationError
            If the validation fails

        """
        if not isinstance(obj, self.internal_type):
            raise ValidationError(obj, self.internal_type)
        return True

    def add_dialect_info(self, dialect, tipo):
        key = self.__module__ + '.' + self.__class__.__name__
        result = {'fqn': key, 'python': self.internal_type, 'type': tipo}
        self.internal_dialect[dialect] = result
        return result

    @classmethod
    def isconfiguration(cls):
        return False

    def potential_tags(self):
        return {}

    def descriptive_name(self):
        return self.name()


class AutoDataType(DataType):
    """Data type for types that are its own python type"""
    def __init__(self, *args, **kwargs):
        super(AutoDataType, self).__init__(ptype=self.__class__, **kwargs)


class AnyType(DataType):
    """Type representing anything"""
    def __init__(self):
        super(AnyType, self).__init__(ptype=self.__class__)

    def convert(self, obj):
        return obj

    def validate(self, obj):
        return True


class NullType(DataType):
    """Data type for None."""
    def __init__(self):
        super(NullType, self).__init__(type(None))

    def convert(self, obj):
        return None

    def validate(self, obj):
        if obj is not None:
            raise ValidationError
        return True


class PlainPythonType(DataType):
    """Data type for Python basic types."""
    def __init__(self, ref=None, validator=None):
        stype = type(ref)
        if ref is None:
            default = None
        else:
            default = stype()

        if validator is None:
            self.custom_validator = None
        elif callable(validator):
            self.custom_validator = validator
        else:
            raise TypeError('validator must be callable or None')

        super(PlainPythonType, self).__init__(stype, default=default)

    def query(self, name, dal, ob, options=True):

        try:
            return self.query_on_ob(name, ob)
        except NoResultFound:
            pass

        #param = dal.search_param_req_tags(req, ob.instrument,
        #                                      ob.mode, ob.tags, ob.pipeline)
        param = dal.search_parameter(name, self, ob)
        return param.content

    def convert(self, obj):
        pre = self.internal_type(obj)

        if self.custom_validator is not None:
            m = self.custom_validator(pre)
            return m

        return pre

    def __str__(self):
        sclass = type(self).__name__
        return "%s[%s]" % (sclass, self.internal_type)


class ListOfType(DataType):
    """Data type for lists of other types."""
    def __init__(self, ref, index=0, nmin=None, nmax=None, accept_scalar=False):
        from numina.core.validator import range_validator
        stype = list
        if inspect.isclass(ref):
            self.internal = ref()
        else:
            self.internal = ref
        super(ListOfType, self).__init__(stype)
        self.index = index
        self.nmin = nmin
        self.nmax = nmax
        self.accept_scalar = accept_scalar
        self.len_validator = range_validator(minval=nmin, maxval=nmax)

    def convert(self, obj):
        if not isinstance(obj, collections.Iterable):
            if self.accept_scalar:
                obj = [obj]
            else:
                raise TypeError("The object received should be iterable"
                                " or the type modified to accept scalar values")

        result = [self.internal.convert(o) for o in obj]
        self.len_validator(len(result))
        return result

    def validate(self, obj):
        for o in obj:
            self.internal.validate(o)
        self.len_validator(len(obj))
        return True

    def _datatype_dump(self, objs, where):
        result = []
        old_dest = where.destination
        for idx, obj in enumerate(objs, start=self.index):
            where.destination = '{}{}'.format(old_dest, idx)
            res = self.internal._datatype_dump(obj, where)
            result.append(res)
        where.destination = old_dest
        return result

    def __str__(self):
        sclass = type(self).__name__
        return "%s[%s]" % (sclass, self.internal)
