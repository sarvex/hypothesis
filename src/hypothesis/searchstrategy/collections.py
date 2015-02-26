# coding=utf-8

# Copyright (C) 2013-2015 David R. MacIver (david@drmaciver.com)

# This file is part of Hypothesis (https://github.com/DRMacIver/hypothesis)

# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at http://mozilla.org/MPL/2.0/.

# END HEADER

from __future__ import division, print_function, absolute_import, \
    unicode_literals

import hypothesis.params as params
import hypothesis.internal.distributions as dist
from hypothesis.internal.compat import hrange
from hypothesis.internal.fixers import nice_string
from hypothesis.searchstrategy.strategies import SearchStrategy, \
    MappedSearchStrategy, check_type, check_length, check_data_type, \
    one_of_strategies, strategy
import hypothesis.settings as hs


class mix_generators(object):

    """a generator which cycles through these generator arguments.

    Will return all the same values as (x for g in generators for x in
    g) but will do so in an order that mixes the different generators
    up.

    """

    def __init__(self, generators):
        self.generators = list(generators)
        self.next_batch = []
        self.solo_generator = None

    def __iter__(self):
        return self

    def next(self):  # pragma: no cover
        return self.__next__()

    def __next__(self):
        if self.solo_generator is None and len(
            self.generators + self.next_batch
        ) == 1:
            self.solo_generator = (self.generators + self.next_batch)[0]

        if self.solo_generator is not None:
            return next(self.solo_generator)

        while self.generators or self.next_batch:
            if not self.generators:
                self.generators = self.next_batch
                self.generators.reverse()
                self.next_batch = []
            g = self.generators.pop()
            try:
                result = next(g)
                self.next_batch.append(g)
                return result
            except StopIteration:
                pass
        raise StopIteration()


class TupleStrategy(SearchStrategy):

    """A strategy responsible for fixed length tuples based on heterogenous
    strategies for each of their elements.

    This also handles namedtuples

    """

    def __init__(self,
                 strategies, tuple_type):
        SearchStrategy.__init__(self)
        strategies = tuple(strategies)
        self.tuple_type = tuple_type
        self.descriptor = self.newtuple([s.descriptor for s in strategies])
        self.element_strategies = strategies
        self.parameter = params.CompositeParameter(
            x.parameter for x in self.element_strategies
        )
        self.size_lower_bound = 1
        self.size_upper_bound = 1
        for e in self.element_strategies:
            self.size_lower_bound *= e.size_lower_bound
            self.size_upper_bound *= e.size_upper_bound

    def reify(self, value):
        return self.newtuple(
            e.reify(v) for e, v in zip(self.element_strategies, value)
        )

    def decompose(self, value):
        return [
            (s.descriptor, v)
            for s, v in zip(self.element_strategies, value)]

    def newtuple(self, xs):
        """Produce a new tuple of the correct type."""
        if self.tuple_type == tuple:
            return tuple(xs)
        else:
            return self.tuple_type(*xs)

    def produce_template(self, random, pv):
        es = self.element_strategies
        return self.newtuple([
            g.produce_template(random, v)
            for g, v in zip(es, pv)
        ])

    def simplify(self, x):
        """
        Defined simplification for tuples: We don't change the length of the
        tuple we only try to simplify individual elements of it.
        We first try simplifying each index. We then try pairs of indices.
        After that we stop because it's getting silly.
        """
        generators = []

        def simplify_single(i):
            for s in self.element_strategies[i].simplify(x[i]):
                z = list(x)
                z[i] = s
                yield self.newtuple(z)

        for i in hrange(0, len(x)):
            generators.append(simplify_single(i))

        return mix_generators(generators)

    def to_basic(self, value):
        return [
            f.to_basic(v)
            for f, v in zip(self.element_strategies, value)
        ]

    def from_basic(self, value):
        check_length(len(self.element_strategies), value)
        return self.newtuple(
            f.from_basic(v)
            for f, v in zip(self.element_strategies, value)
        )


class ListStrategy(SearchStrategy):

    """A strategy for lists which takes an intended average length and a
    strategy for each of its element types and generates lists containing any
    of those element types.

    The conditional distribution of the length is geometric, and the
    conditional distribution of each parameter is whatever their
    strategies define.

    """

    def __init__(self,
                 strategies, average_length=50.0):
        SearchStrategy.__init__(self)

        self.descriptor = [x.descriptor for x in strategies]
        if self.descriptor:
            self.element_strategy = one_of_strategies(strategies)
            self.parameter = params.CompositeParameter(
                average_length=params.ExponentialParameter(
                    1.0 / average_length),
                child_parameter=self.element_strategy.parameter,
            )
        else:
            self.size_upper_bound = 1
            self.size_lower_bound = 1
            self.parameter = params.CompositeParameter()

    def decompose(self, value):
        return [
            (self.element_strategy.descriptor, v)
            for v in value
        ]

    def reify(self, value):
        if value:
            return list(map(self.element_strategy.reify, value))
        else:
            return []

    def produce_template(self, random, pv):
        if not self.descriptor:
            return ()
        length = dist.geometric(random, 1.0 / (1 + pv.average_length))
        result = []
        for _ in hrange(length):
            result.append(
                self.element_strategy.produce_template(
                    random, pv.child_parameter))
        return tuple(result)

    def simplify(self, x):
        assert isinstance(x, tuple)
        if not x:
            return

        yield ()

        for i in hrange(0, len(x)):
            if len(x) > 1:
                y = list(x)
                del y[i]
                yield tuple(y)
            for s in self.element_strategy.simplify(x[i]):
                z = list(x)
                z[i] = s
                yield tuple(z)

        for i in hrange(0, len(x) - 1):
            z = list(x)
            del z[i]
            del z[i]
            yield tuple(z)

    def to_basic(self, value):
        check_type(tuple, value)
        if not self.descriptor:
            return []
        return list(map(self.element_strategy.to_basic, value))

    def from_basic(self, value):
        check_data_type(list, value)
        if not self.descriptor:
            return ()
        return tuple(map(self.element_strategy.from_basic, value))


class SetStrategy(MappedSearchStrategy):

    """A strategy for sets of values, defined in terms of a strategy for lists
    of values."""

    def __init__(self, strategies):
        strategies = list(strategies)
        strategies.sort(key=nice_string)

        self.descriptor = {x.descriptor for x in strategies}
        if self.descriptor:
            self.element_strategy = one_of_strategies(strategies)
            self.parameter = params.CompositeParameter(
                stopping_chance=params.UniformFloatParameter(0.01, 0.25),
                child_parameter=self.element_strategy.parameter,
            )
            self.size_lower_bound = (
                2 ** self.element_strategy.size_lower_bound)
            self.size_upper_bound = (
                2 ** self.element_strategy.size_upper_bound)
        else:
            self.parameter = params.CompositeParameter()
            self.size_lower_bound = 1
            self.size_upper_bound = 1

    def decompose(self, value):
        return [
            (self.element_strategy.descriptor, v)
            for v in value
        ]

    def reify(self, value):
        if not self.descriptor:
            return set()
        return set(map(self.element_strategy.reify, value))

    def produce_template(self, random, pv):
        if not self.descriptor:
            return frozenset()
        result = set()
        while True:
            if dist.biased_coin(random, pv.stopping_chance):
                break
            result.add(self.element_strategy.produce_template(
                random, pv.child_parameter
            ))
        return frozenset(result)

    def simplify(self, x):
        assert isinstance(x, frozenset)
        if not x:
            return

        yield frozenset()

        for v in x:
            y = set(x)
            y.remove(v)
            yield frozenset(y)
            for w in self.element_strategy.simplify(v):
                z = set(y)
                z.add(w)
                yield frozenset(z)

    def to_basic(self, value):
        check_type(frozenset, value)
        if not self.descriptor:
            return []
        return list(map(self.element_strategy.to_basic, value))

    def from_basic(self, value):
        if not self.descriptor:
            return frozenset()
        check_data_type(list, value)
        return frozenset(map(self.element_strategy.from_basic, value))


class FrozenSetStrategy(MappedSearchStrategy):

    """A strategy for frozensets of values, defined in terms of a strategy for
    lists of values."""

    def __init__(self, set_strategy):
        super(FrozenSetStrategy, self).__init__(
            strategy=set_strategy,
            descriptor=frozenset(set_strategy.descriptor)
        )

    def pack(self, x):
        return frozenset(x)


class FixedKeysDictStrategy(MappedSearchStrategy):

    """A strategy which produces dicts with a fixed set of keys, given a
    strategy for each of their equivalent values.

    e.g. {'foo' : some_int_strategy} would
    generate dicts with the single key 'foo' mapping to some integer.

    """

    def __init__(self, strategy_dict):
        self.keys = tuple(sorted(
            strategy_dict.keys(), key=nice_string
        ))
        super(FixedKeysDictStrategy, self).__init__(
            descriptor={
                k: v.descriptor for k, v in strategy_dict.items()
            },
            strategy=TupleStrategy(
                (strategy_dict[k] for k in self.keys), tuple
            )
        )

    def pack(self, value):
        return dict(zip(self.keys, value))


@strategy.extend(set)
def define_set_strategy(descriptor, settings):
    return SetStrategy(strategy(d, settings) for d in descriptor)


@strategy.extend(frozenset)
def define_frozen_set_strategy(descriptor, settings):
    return FrozenSetStrategy(strategy(set(descriptor), settings))


@strategy.extend(list)
def define_list_strategy(descriptor, settings):
    return ListStrategy(
        [strategy(d, settings) for d in descriptor],
        average_length=settings.average_list_length
    )

hs.define_setting(
    "average_list_length",
    default=50.0,
    description="Average length of lists to use"
)


@strategy.extend(tuple)
def define_tuple_strategy(descriptor, settings):
    return TupleStrategy(
        tuple(strategy(d, settings) for d in descriptor),
        tuple_type=type(descriptor)
    )


@strategy.extend(dict)
def define_dict_strategy(descriptor, settings):
    strategy_dict = {}
    for k, v in descriptor.items():
        strategy_dict[k] = strategy(v, settings)
    return FixedKeysDictStrategy(strategy_dict)