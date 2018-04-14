

import ast
from inspect import signature
from inspect import Parameter
from types import FunctionType as function
import math
from collections import OrderedDict

from jamovi.core import MeasureType
from ..utils import FValues
from ..utils import convert
from ..utils import is_missing
from ..utils import get_missing
from . import functions

NaN = float('nan')


class Num(ast.Num):

    def __init__(self, *args, **kwargs):
        ast.Num.__init__(self, *args, **kwargs)
        self._node_parents = [ ]

    def _add_node_parent(self, node):
        self._node_parents.append(node)

    def _remove_node_parent(self, node):
        self._node_parents.remove(node)

    def fvalue(self, index):
        return self.n

    def fvalues(self):
        return FValues(self)

    def is_atomic_node(self):
        return True

    @property
    def measure_type(self):
        if isinstance(self.n, int):
            return MeasureType.ORDINAL
        else:
            return MeasureType.CONTINUOUS

    @property
    def needs_recalc(self):
        return False

    @needs_recalc.setter
    def needs_recalc(self, needs_recalc: bool):
        pass

    @property
    def has_levels(self):
        return False


class Str(ast.Str):

    def __init__(self, *args, **kwargs):
        ast.Str.__init__(self, *args, **kwargs)
        self._node_parents = [ ]

    def _add_node_parent(self, node):
        self._node_parents.append(node)

    def _remove_node_parent(self, node):
        self._node_parents.remove(node)

    def fvalue(self, index):
        return self.s

    def fvalues(self):
        return FValues(self)

    def is_atomic_node(self):
        return True

    @property
    def measure_type(self):
        return MeasureType.NOMINAL_TEXT

    @property
    def needs_recalc(self):
        return False

    @needs_recalc.setter
    def needs_recalc(self, needs_recalc: bool):
        pass

    @property
    def has_levels(self):
        return True

    @property
    def levels(self):
        return [ self.s ]


class UnaryOp(ast.UnaryOp):

    def __init__(self, *args, **kwargs):
        ast.UnaryOp.__init__(self, *args, **kwargs)
        self._node_parents = [ ]

    def fvalue(self, index):
        op = self.op
        v = self.operand.fvalue(index)
        if isinstance(op, ast.USub):
            if is_missing(v):
                return v
            elif isinstance(v, tuple):
                v = v[0]
            return -v
        elif isinstance(op, ast.UAdd):
            if is_missing(v):
                return v
            elif isinstance(v, tuple):
                v = v[0]
            elif isinstance(v, str):
                v = float(v)
            return v
        elif isinstance(op, ast.Not):
            if is_missing(v):
                return v
            elif isinstance(v, tuple):
                v = v[0]
            return 1 if not v else 0
        elif isinstance(op, ast.Invert):
            if is_missing(v):
                return 0
            else:
                return v
        else:
            raise RuntimeError("Shouldn't get here")

    def fvalues(self):
        return FValues(self)

    def is_atomic_node(self):
        return self.operand.is_atomic_node()

    @property
    def measure_type(self):
        if isinstance(self.op, ast.UAdd):
            if self.operand.measure_type is MeasureType.NOMINAL_TEXT:
                return MeasureType.CONTINUOUS
            else:
                return self.operand.measure_type
        elif isinstance(self.op, ast.Not):
            return MeasureType.NOMINAL
        else:
            return self.operand.measure_type

    def _add_node_parent(self, node):
        self._node_parents.append(node)

    def _remove_node_parent(self, node):
        self._node_parents.remove(node)

    @property
    def needs_recalc(self):
        return False

    @needs_recalc.setter
    def needs_recalc(self, needs_recalc: bool):
        for parent in self._node_parents:
            parent.needs_recalc = needs_recalc

    @property
    def has_levels(self):
        return False


class BoolOp(ast.BoolOp):

    def __init__(self, *args, **kwargs):
        ast.BoolOp.__init__(self, *args, **kwargs)
        self._node_parents = [ ]

    def fvalue(self, index):
        if isinstance(self.op, ast.And):
            to_return = 1
            for v in self.values:
                value = v.fvalue(index)
                if is_missing(value):
                    to_return = value
                    continue
                if isinstance(value, tuple):
                    value = value[0]
                if not value:
                    return 0
            return to_return
        elif isinstance(self.op, ast.Or):
            to_return = 0
            for v in self.values:
                value = v.fvalue(index)
                if is_missing(value):
                    to_return = value
                    continue
                if isinstance(value, tuple):
                    value = value[0]
                if value:
                    return 1
            return to_return
        else:
            raise RuntimeError("Shouldn't get here")

    def fvalues(self):
        return FValues(self)

    def is_atomic_node(self):
        for value in self.values:
            if not value.is_atomic_node():
                return False
        return True

    @property
    def measure_type(self):
        return MeasureType.NOMINAL

    def _add_node_parent(self, node):
        self._node_parents.append(node)

    def _remove_node_parent(self, node):
        self._node_parents.remove(node)

    @property
    def needs_recalc(self):
        return False

    @needs_recalc.setter
    def needs_recalc(self, needs_recalc: bool):
        for parent in self._node_parents:
            parent.needs_recalc = needs_recalc

    @property
    def has_levels(self):
        return False


class Call(ast.Call):

    def __init__(self, func, args, keywords):
        self._cached_value = None
        self._node_parents = [ ]
        self._arg_types = [ ]
        self._m_type = None

        name = func.id

        if not hasattr(functions, name):
            raise NameError('Function {}() does not exist'.format(name))

        self._function = getattr(functions, name)

        # the following checks and substitutes sub-functions
        # see BOXCOX() or Z() for an example of a function
        # with sub-functions

        params = signature(self._function).parameters
        param_names = list(params)
        if self._function.meta.is_row_wise:
            param_names.pop(0)

        for param_name in param_names:
            param = params[param_name]
            annot = param.annotation
            if annot is Parameter.empty:
                annot = None
            self._arg_types.append(annot)

        sub_arg_len = len(param_names)
        for param_name in reversed(param_names):
            if isinstance(params[param_name].default, function):
                sub_arg_len -= 1
            else:
                break
        sub_func_args = args[0:sub_arg_len]  # arguments to the sub-functions

        for i in range(sub_arg_len, len(param_names)):
            # add the sub-functions as arguments if not specified by the user
            if len(args) <= i:
                sub_func = params[param_names[i]].default
                sub_func_name = sub_func.__name__
                new_func = Call(ast.Name(id=sub_func_name), sub_func_args, keywords)
                for sf_arg in sub_func_args:
                    sf_arg._add_node_parent(new_func)
                args.append(new_func)
            else:
                break

        ast.Call.__init__(self, func, args, keywords)

    def fvalue(self, index):
        if self._function.__name__ == 'OFFSET':
            offset = convert(self.args[1].fvalue(index), int)
            if index < offset:
                value = NaN
            else:
                value = self.args[0].fvalue(index - offset)
        elif self._function.meta.is_column_wise:
            if self._cached_value is None:
                args = list(map(lambda arg: arg.fvalues(), self.args))
                for i in range(len(args)):
                    arg_type_i = min(i, len(self._arg_types) - 1)
                    arg_type = self._arg_types[arg_type_i]
                    args[i] = (convert(v, arg_type) for v in args[i])
                self._cached_value = self._function(*args)
            value = self._cached_value
        else:
            args = list(map(lambda arg: arg.fvalue(index), self.args))
            for i in range(len(args)):
                arg_type_i = min(i, len(self._arg_types) - 1)
                arg_type = self._arg_types[arg_type_i]
                args[i] = convert(args[i], arg_type)
            value = self._function(index, *args)

        return value

    def fvalues(self):
        return FValues(self)

    def is_atomic_node(self):
        return False

    def _add_node_parent(self, node):
        self._node_parents.append(node)

    def _remove_node_parent(self, node):
        self._node_parents.remove(node)

    @property
    def needs_recalc(self):
        return self._cached_value is None

    @needs_recalc.setter
    def needs_recalc(self, needs_recalc: bool):
        if needs_recalc:
            self._cached_value = None
        for parent in self._node_parents:
            parent.needs_recalc = needs_recalc

    @property
    def measure_type(self):
        # determine the measure type from the function meta
        # and the function arguments

        if self._m_type is not None:  # value cached
            return self._m_type

        func_meta = self._function.meta

        if len(self.args) == 0:
            return func_meta.m_type

        if len(func_meta.returns) == 0:
            return func_meta.m_type

        # not sure why this doesn't work:
        # types = map(lambda i: args[i].measure_type, self._returns)
        #
        # had to do this instead:
        types = [None] * len(func_meta.returns)
        for i in range(len(func_meta.returns)):
            arg_i = func_meta.returns[i]
            if arg_i < len(self.args):
                types[i] = self.args[arg_i].measure_type

        mt = MeasureType.NOMINAL
        for t in list(types):
            if t is MeasureType.ORDINAL and mt is MeasureType.NOMINAL:
                mt = MeasureType.ORDINAL
            elif t is MeasureType.CONTINUOUS:
                mt = MeasureType.CONTINUOUS
            elif t is MeasureType.NOMINAL_TEXT:
                mt = MeasureType.NOMINAL_TEXT
                break

        self._m_type = mt  # cache value
        return mt

    @property
    def has_levels(self):
        return True

    @property
    def levels(self):

        m_type = self.measure_type
        func_meta = self._function.meta
        arg_level_indices = func_meta.arg_level_indices

        if len(arg_level_indices) == 0:
            return [ ]
        if len(self.args) == 0:
            return [ ]
        if m_type is not MeasureType.NOMINAL_TEXT:
            return [ ]

        level_use = OrderedDict()

        for i in range(len(arg_level_indices)):
            arg_i = arg_level_indices[i]
            if arg_i < len(self.args):
                arg = self.args[arg_i]
                if not arg.has_levels:
                    continue
                for level in arg.levels:
                    level_use[level[1]] = 0

        for value in self.fvalues():
            if is_missing(value):
                continue
            value = convert(value, str)
            if value in level_use:
                level_use[value] += 1

        levels = list(filter(lambda k: level_use[k] > 0, level_use))
        for i in range(len(levels)):
            levels[i] = (i, levels[i])

        return levels


class BinOp(ast.BinOp):

    def __init__(self, *args, **kwargs):
        ast.BinOp.__init__(self, *args, **kwargs)
        self._node_parents = [ ]

    def fvalue(self, index):

        lv = self.left
        rv = self.right
        op = self.op

        mt = self.measure_type
        if mt is MeasureType.CONTINUOUS:
            ul_type = float
        elif mt is MeasureType.NOMINAL_TEXT:
            ul_type = str
        else:
            ul_type = int

        if isinstance(lv, ast.Num):
            lv = lv.n
        elif hasattr(lv, 'fvalue'):
            lv = lv.fvalue(index)

        lv = convert(lv, ul_type)

        if isinstance(rv, ast.Num):
            rv = rv.n
        elif hasattr(rv, 'fvalue'):
            rv = rv.fvalue(index)

        rv = convert(rv, ul_type)

        if ul_type is str and isinstance(op, ast.Add):
            pass
        else:
            if is_missing(lv) or is_missing(rv):
                return get_missing(int)

        if isinstance(op, ast.Add):
            return lv + rv
        elif isinstance(op, ast.Sub):
            return lv - rv
        elif isinstance(op, ast.Mult):
            return lv * rv
        elif isinstance(op, ast.Div):
            try:
                return lv / rv
            except ZeroDivisionError:
                return get_missing()
        elif isinstance(op, ast.Mod):
            return lv % rv
        elif isinstance(op, ast.Pow):
            return lv ** rv
        elif isinstance(op, ast.BitXor):
            return lv ** rv
        else:
            return get_missing()

    def fvalues(self):
        return FValues(self)

    def is_atomic_node(self):
        return self.left.is_atomic_node() and self.right.is_atomic_node()

    @property
    def measure_type(self):

        if isinstance(self.op, ast.Pow):
            return MeasureType.CONTINUOUS

        lmt = self.left.measure_type
        rmt = self.right.measure_type

        if lmt is MeasureType.NOMINAL_TEXT or rmt is MeasureType.NOMINAL_TEXT:
            return MeasureType.NOMINAL_TEXT
        elif lmt is MeasureType.CONTINUOUS or rmt is MeasureType.CONTINUOUS:
            return MeasureType.CONTINUOUS
        elif lmt is MeasureType.NOMINAL or rmt is MeasureType.NOMINAL:
            return MeasureType.NOMINAL
        else:
            return MeasureType.ORDINAL

    def _add_node_parent(self, node):
        self._node_parents.append(node)

    def _remove_node_parent(self, node):
        self._node_parents.remove(node)

    @property
    def needs_recalc(self):
        return False

    @needs_recalc.setter
    def needs_recalc(self, needs_recalc: bool):
        for parent in self._node_parents:
            parent.needs_recalc = needs_recalc

    @property
    def has_levels(self):
        return False


class Compare(ast.Compare):

    def __init__(self, *args, **kwargs):
        ast.Compare.__init__(self, *args, **kwargs)
        self._node_parents = [ ]

    def fvalue(self, index):
        v1 = self.left.fvalue(index)
        if is_missing(v1):
            return get_missing(int)
        for i in range(len(self.ops)):
            op = self.ops[i]
            v2 = self.comparators[i].fvalue(index)
            if is_missing(v2):
                return get_missing(int)
            if not Compare._test(v1, op, v2):
                return 0
            v1 = v2
        return 1

    @staticmethod
    def _test(v1, op, v2):

        if isinstance(v1, tuple) and isinstance(v2, tuple):
            v1 = convert(v1, int)
            v2 = convert(v2, int)
        elif isinstance(v1, tuple):
            v1 = v1[1 if isinstance(v2, str) else 0]
        elif isinstance(v2, tuple):
            v2 = v2[1 if isinstance(v1, str) else 0]

        if isinstance(op, ast.Lt):
            return v1 < v2
        elif isinstance(op, ast.Gt):
            return v1 > v2
        elif isinstance(op, ast.GtE):
            return v1 >= v2
        elif isinstance(op, ast.LtE):
            return v1 <= v2
        elif isinstance(op, ast.Eq):
            if isinstance(v1, float) or isinstance(v2, float):
                return math.isclose(float(v1), float(v2))
            else:
                return v1 == v2
        elif isinstance(op, ast.NotEq):
            if isinstance(v1, float) or isinstance(v2, float):
                return not math.isclose(float(v1), float(v2))
            else:
                return v1 != v2
        else:
            raise RuntimeError("Shouldn't get here")

    def fvalues(self):
        return FValues(self)

    def is_atomic_node(self):
        if self.left.is_atomic_node() is False:
            return False
        for node in self.comparators:
            if node.is_atomic_node() is False:
                return False
        return True

    @property
    def measure_type(self):
        return MeasureType.NOMINAL

    def _add_node_parent(self, node):
        self._node_parents.append(node)

    def _remove_node_parent(self, node):
        self._node_parents.remove(node)

    @property
    def needs_recalc(self):
        return False

    @needs_recalc.setter
    def needs_recalc(self, needs_recalc: bool):
        for parent in self._node_parents:
            parent.needs_recalc = needs_recalc

    @property
    def has_levels(self):
        return True

    @property
    def levels(self):
        return ((1, 'true'), (0, 'false'))
