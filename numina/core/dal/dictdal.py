#
# Copyright 2015 Universidad Complutense de Madrid
#
# This file is part of Numina
#
# Numina is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Numina is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Numina.  If not, see <http://www.gnu.org/licenses/>.
#

'''DAL for dictionary-based database of products.'''

from numina.core import import_object
from numina.core import ObservationResult
from numina.core import init_drp_system
from numina.core import fully_qualified_name
from numina.core.dal import AbsDAL
from numina.core.dal import NoResultFound
from numina.core.dal import ObservingBlock
from numina.core.dal import StoredProduct, StoredParameter
from numina.core.recipeinput import RecipeInputBuilderGTC

from numina.store import load
from numina.store import init_store_backends

def product_label(drp, klass):
    fqn = fully_qualified_name(klass)
    for p in drp.products:
        if p['name'] == fqn:
            return p['alias']
    else:
        return klass.__name__
        

def tags_are_valid(subset, superset):
    for key, val in subset.items():
        if key in superset and superset[key] != val:
            return False
    return True

class DictDAL(AbsDAL):
    def __init__(self, base):
        super(DictDAL, self).__init__()

        self.args_drps = init_drp_system()
        init_store_backends()
        # Check that the structure de base is correct
        self._base = base

    def search_oblock_from_id(self, obsid):
        ob_table = self._base['oblocks']
        try:
            ob = ob_table[obsid]
            return ObservingBlock(**ob)
        except KeyError:
            raise NoResultFound("oblock with id %d not found", obsid)

    def search_recipe(self, ins, mode, pipeline):
        recipe_fqn = self.search_recipe_fqn(ins, mode, pipeline)
        Klass = import_object(recipe_fqn)
        return Klass

    def search_recipe_fqn(self, ins, mode, pipename):
        drp = self.args_drps[ins]
        this_pipeline = drp.pipelines[pipename]
        recipes = this_pipeline.recipes
        recipe_fqn = recipes[mode]
        return recipe_fqn

    def search_recipe_from_ob(self, ob, pipeline):
        ins = ob.instrument
        mode = ob.mode
        return self.search_recipe(ins, mode, pipeline)

    def search_rib_from_ob(self, obsres, pipeline):
        return RecipeInputBuilderGTC

    def search_prod_obsid(self, ins, obsid, pipeline):
        '''Returns the first coincidence...'''
        products = self._base['products']
        ins_prod = products[ins]

        # search results of these OBs
        for prod in ins_prod.values():
            if prod['ob'] == obsid:
                # We have found the result, no more checks
                return StoredProduct(**prod)
        else:
            raise NoResultFound('result for ob %i not found' % obsid)

    def search_prod_req_tags(self, req, ins, tags, pipeline):
        return self.search_prod_type_tags(req.type, ins, tags, pipeline)

    def search_prod_type_tags(self, tipo, ins, tags, pipeline):
        '''Returns the first coincidence...'''
        reqs = self._base['requirements']
        products = reqs['products']
        ins_prod = products

        klass = tipo.__class__
        label = product_label(self.args_drps[ins], klass)

        # search results of these OBs
        for prod in ins_prod:
            pk = prod['type'] 
            pt = prod['tags']
            if pk == label and tags_are_valid(pt, tags):
                # this is a valid product
                # We have found the result, no more checks
                prod['id'] = 1
                prod['content'] = load(tipo, prod['content'])
                return StoredProduct(**prod)
        else:
            msg = 'type %s compatible with tags %r not found' % (klass, tags)
            raise NoResultFound(msg)
    
    def search_param_req(self, req, instrument, mode, pipeline):
        reqs = self._base['requirements']
        
        parameters = reqs['parameters']

        for p in parameters:
            if p['key'] == req.dest and p['mode'] == mode:
                content = StoredParameter(p['value'])
                return content
        else:
            raise NoResultFound("No parameters for %s mode, pipeline %s", mode, pipeline)            

    def obsres_from_oblock_id(self, obsid):
        este = self._base['oblocks']
        h = ObservationResult(obsid)
        h.instrument = este['instrument']
        h.mode = este['mode']
        h.parent = None
        h.tags = {}
        h.files = este['frames']
        h.children = []

        this_drp = self.args_drps[h.instrument]
        tagger_fqn = None
        for mode in this_drp.modes:
            if mode.key == h.mode:
                tagger_fqn = mode.tagger
                break
        else:
            raise ValueError('no mode for %s' % h.mode)

        if tagger_fqn is None:
            master_tags = {}
        else:
            tagger_for_this_mode = import_object(tagger_fqn)
            master_tags = tagger_for_this_mode(h)

        h.tags = master_tags
        return h
