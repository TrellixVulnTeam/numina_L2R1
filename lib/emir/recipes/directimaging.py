#
# Copyright 2008-2010 Sergio Pascual
# 
# This file is part of PyEmir
# 
# PyEmir is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# PyEmir is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with PyEmir.  If not, see <http://www.gnu.org/licenses/>.
#

'''Recipe for the reduction of imaging mode observations.'''

import logging
import os

import numpy
import pyfits
import scipy
import scipy.signal


import numina.image
import numina.qa
from numina.image import DiskImage
from numina.image.flow import SerialFlow
from numina.image.processing import DarkCorrector, NonLinearityCorrector, FlatFieldCorrector
from numina.logger import captureWarnings
from numina.array.combine import median
from numina.array import subarray_match
from numina.worker import para_map
from numina.array import combine_shape, resize_array, correct_flatfield
from numina.array.combine import flatcombine, combine
from numina.array import compute_median_background, compute_sky_advanced, create_object_mask
from numina.array import SextractorConf
from numina.image.imsurfit import imsurfit
from numina.recipes import RecipeBase, RecipeError
from numina.recipes.registry import ProxyPath, ProxyQuery
from numina.recipes.registry import Schema

from emir.dataproducts import create_result, create_raw
from emir.recipes import EmirRecipeMixin

_logger = logging.getLogger("emir.recipes")

def _name_redimensioned_images(label, iteration, ext='.fits'):
    dn = '%s_r_i%01d%s' % (label, iteration, ext)
    mn = '%s_mr_i%01d%s' % (label, iteration, ext)
    return dn, mn

def _name_object_mask(label, iteration, ext='.fits'):
    return '%s_mro_i%01d%s' % (label, iteration, ext)

def _name_skyflat_proc(label, iteration, ext='.fits'):
    dn = '%s_rf_i%01d%s' % (label, iteration, ext)
    return dn

def _name_skysub(label, iteration, ext='.fits'):
    dn = '%s_rfs_i%01d%s' % (label, iteration, ext)
    return dn

def _name_skyflat(label, iteration, ext='.fits'):
    dn = 'superflat_%s_i%01d%s' % (label, iteration, ext)
    return dn


class ImageInformation(object):
    def __init__(self):
        pass


class Recipe(RecipeBase, EmirRecipeMixin):
    '''Recipe for the reduction of imaging mode observations.

    Recipe to reduce observations obtained in imaging mode, considering different
    possibilities depending on the size of the offsets between individual images.
    In particular, the following observing modes are considered: stare imaging, nodded
    beamswitched imaging, and dithered imaging. 
    
    A critical piece of information here is a table that clearly specifies which
    images can be labeled as *science*, and which ones as *sky*. Note that some
    images are used both as *science* and *sky* (when the size of the targets are
    small compared to the offsets).
    
    **Observing modes:**
     
     * StareImage
     * Nodded/Beam-switched images
     * Dithered images 
    
    
    **Inputs:**
    
     * Science frames + [Sky Frames]
     * Observing mode name: **stare image**, **nodded beamswitched image**, or **dithered imaging**
     * A table relating each science image with its sky image(s) (TBD if it's in 
       the FITS header and/or in other format)
     * Offsets between them (Offsets must be integer)
     * Master Dark 
     * Bad pixel mask (BPM) 
     * Non-linearity correction polynomials 
     * Master flat (twilight/dome flats)
     * Master background (thermal background, only in K band)
     * Exposure Time (must be the same in all the frames)
     * Airmass for each frame
     * Detector model (gain, RN, lecture mode)
     * Average extinction in the filter
     * Astrometric calibration (TBD)
    
    **Outputs:**
    
     * Image with three extensions: final image scaled to the individual exposure
       time, variance  and exposure time map OR number of images combined (TBD)
    
    **Procedure:**
    
    Images are corrected from dark, non-linearity and flat. Then, an iterative
    process starts:
    
     * Sky is computed from each frame, using the list of sky images of each
       science frame. The objects are avoided using a mask (from the second
       iteration on).
    
     * The relative offsets are the nominal from the telescope. From the second
       iteration on, we refine them using objects of appropriate brightness (not
       too bright, not to faint).
    
     * We combine the sky-subtracted images, output is: a new image, a variance
       image and a exposure map/number of images used map.
    
     * An object mask is generated.
    
     * We recompute the sky map, using the object mask as an additional input. From
       here we iterate (typically 4 times).
    
     * Finally, the images are corrected from atmospheric extinction and flux
       calibrated.
    
     * A preliminary astrometric calibration can always be used (using the central
       coordinates of the pointing and the plate scale in the detector). A better
       calibration might be computed using available stars (TBD).
    
    '''
    capabilities = ['dithered_images',
                    'nodded-beamswitched_images',
                    'stare_images']
    
    required_parameters = [
        Schema('extinction', ProxyQuery(dummy=1.0), 'Mean atmospheric extinction'),
        Schema('master_bias', ProxyQuery(), 'Master bias image'),
        Schema('master_dark', ProxyQuery(), 'Master dark image'),
        Schema('master_bpm', ProxyQuery(), 'Master bad pixel mask'),
        Schema('master_flat', ProxyQuery(), 'Master flat field image'),
        Schema('nonlinearity', ProxyQuery(dummy=[1.0, 0.0]), 'Polynomial for non-linearity correction'),
        Schema('iterations', 4, 'Iterations of the recipe'),
        Schema('images', ProxyPath('/observing_block/result/images'), 'A list of paths to images'),
        Schema('resultname', 'result.fits', 'Name of the output image'),
        Schema('airmasskey', 'AIRMASS', 'Name of airmass header keyword'),
        Schema('exposurekey', 'EXPOSED', 'Name of exposure header keyword'),
        # Sextractor parameter files
        Schema('sexfile', None, 'Sextractor parameter file'),
        Schema('paramfile', None, 'Sextractor parameter file'),
        Schema('nnwfile', None, 'Sextractor parameter file'),
        Schema('convfile', None, 'Sextractor parameter file'),
    ]
    
    provides = []
    
    def __init__(self, param, runinfo):
        super(Recipe, self).__init__(param, runinfo)

    def setup(self):
        # Parameters will store the image with absolute paths
        self.parameters['master_dark'] = DiskImage(os.path.abspath(self.parameters['master_dark']))
        self.parameters['master_flat'] = DiskImage(os.path.abspath(self.parameters['master_flat']))
        self.parameters['master_bpm'] = DiskImage(os.path.abspath(self.parameters['master_bpm']))
        
        # Converting self.parameters['images'] to DiskImage
        # with absolute path
        r = dict((key, numina.image.DiskImage(filename=os.path.abspath(key))) 
             for key in self.parameters['images'])
        
        for key, val in self.parameters['images'].items():                
            self.parameters['images'][key] = (r[key], val[0], [r[key] for key in val[1]])
        
        d = {}
        for key in ['sexfile', 'paramfile', 'nnwfile', 'convfile']:
            d[key] = os.path.abspath(self.parameters[key])
            
        self.sconf = SextractorConf(**d)
        
    def compute_simple_sky(self, iinfo, iteration=0):
        for image in iinfo:
            #dst = _name_skysub(image.resized_base, 0)
            hdulist1 = pyfits.open(image.lastname, mode='update')
            hdulist2 = pyfits.open(image.resized_mask)
            try:
                data = hdulist1['primary'].data
                mask = hdulist2['primary'].data
                sky = compute_median_background(data, mask, image.region)
                _logger.debug('median sky value is %f', sky)
                image.median_sky = sky
                
                _logger.info('Iter %d, SC: subtracting sky to image %s', 
                             iteration, image.lastname)
                region = image.region
                data[region] -= sky
                
            finally:
                hdulist1.close()
                hdulist2.close()
                
    def combine_images(self, iinfo, iteration=0):
        imgslll = [pyfits.open(image.lastname, mode='readonly', memmap=True) for image in iinfo]
        mskslll = [pyfits.open(image.resized_mask, mode='readonly', memmap=True) for image in iinfo]
        try:
            extinc = [pow(10, 0.4 * image.airmass * self.parameters['extinction']) for image in iinfo]
            data = [i['primary'].data for i in imgslll]
            masks = [i['primary'].data for i in mskslll]
            sf_data = median(data, masks, scales=extinc, dtype='float32')
    
            # We are saving here only data part
            pyfits.writeto('result_i%0d.fits' % 0, sf_data[0], clobber=True)
        finally:
            map(lambda x: x.close(), imgslll)
            map(lambda x: x.close(), mskslll)        

    def compute_superflat(self, iinfo, iteration=0):
        try:
            filelist = []
            data = []
            masks = []
            for image in iinfo:
                hdulist = pyfits.open(image.resized_base, memmap=True, mode='readonly')
                filelist.append(hdulist)
                data.append(hdulist['primary'].data[image.region])
                
                hdulist = pyfits.open(image.resized_mask, memmap=True, mode='readonly')
                filelist.append(hdulist)
                masks.append(hdulist['primary'].data[image.region])

                scales = [image.median_scale for image in iinfo]
            sf_data = flatcombine(data, masks, scales=scales, method='median')
            #sf_data = combine(data, masks=masks, scales=scales, method='median')

            pc, fitted = imsurfit(sf_data[0], order=2, output_fit=True)
            _logger.info('polynomial fit %s', pc)
            #fitted /= fitted.mean()            
            
            sfhdu = pyfits.PrimaryHDU(sf_data[0])            
            sfhdu.writeto(_name_skyflat('comb', 0))
            sfhdu = pyfits.PrimaryHDU(fitted)            
            sfhdu.writeto(_name_skyflat('fit', 0))
            return fitted
        finally:
            for fileh in filelist:
                fileh.close()        

    def correct_superflat(self, iinfo, fitted, iteration=0):
        for image in iinfo:
            _logger.info("Iter %d, SF: apply superflat to image %s", 0, image.resized_base)
            hdulist = pyfits.open(image.resized_base, mode='readonly')
            data = hdulist['primary'].data[image.region]
            newdata = hdulist['primary'].data.copy()
            newdata[image.region] = correct_flatfield(data, fitted)
            newheader = hdulist['primary'].header.copy()
            hdulist.close()
            phdu = pyfits.PrimaryHDU(newdata, newheader)
            image.lastname = _name_skyflat_proc(image.label, 0)
            phdu.writeto(image.lastname)
            
    def run(self):
        
        sortedkeys = sorted(self.parameters['images'].keys())
        
        # Basic processing
        # Open dark and flat
        mdark = pyfits.getdata(self.parameters['master_dark'].filename)
        #mflat = pyfits.getdata(self.parameters['master_flat'].filename)
        # Unused for the momment
        # mbpm = pyfits.getdata(self.parameters['master_bpm'].filename)
        #
        
        images_info = []
        image_shapes = (2048, 2048)
        for key in sortedkeys:
            # Label
            ii = ImageInformation()
            ii.label = os.path.splitext(key)[0]
            ii.base = self.parameters['images'][key][0].filename
            ii.baseshape = image_shapes
            ii.region = None
            ii.resized_base = None
            ii.resized_mask = None
            ii.objmask = None
            hdr = pyfits.getheader(ii.base)
            try:
                ii.airmass = hdr[self.parameters['airmasskey']]
            except KeyError, e:
                raise RecipeError("%s in image %s" % (str(e), ii.base))
            images_info.append(ii)
        
        _logger.info('Basic processing')
        for image in images_info:
            hdulist = pyfits.open(image.base, mode='update')
            try:
                hdu = hdulist['primary']
                _logger.info('Correcting dark %s', image.label)
                if not hdu.header.has_key('NMDARK'):
                    hdu.data -= mdark
                    hdu.header.update('NMDARK', 'done')
            finally:
                hdulist.close()
                
        del mdark
        #del mflat
        
        # Resizing images
        offsets = [self.parameters['images'][key][1] for key in sortedkeys]
        
        finalshape, offsetsp = combine_shape(image_shapes, offsets)
        _logger.info('Shape of resized array is %s', finalshape)
        
        _logger.info('Resizing images')
        for image, noffset in zip(images_info, offsetsp):
            shape = image_shapes
            region, _ = subarray_match(finalshape, noffset, shape)
            image.region = region
            imgn, maskn = _name_redimensioned_images(image.label, 0)
            image.resized_base = imgn
            image.resized_mask = maskn
            _logger.info('Resizing image %s', image.base)
            
            hdulist = pyfits.open(image.base, mode='readonly')
            try:
                hdu = hdulist['primary']
                basedata = hdu.data
                newdata = resize_array(basedata, finalshape, region)                
                newhdu = pyfits.PrimaryHDU(newdata, hdu.header)                
                _logger.info('Saving %s', imgn)
                newhdu.writeto(imgn)
            finally:
                hdulist.close()
        
            _logger.info('Resizing mask %s', image.label)
            # FIXME, we should open the base mask
            hdulist = pyfits.open(image.base, mode='readonly')
            try:
                hdu = hdulist['primary']
                # FIXME
                basedata = numpy.zeros(shape, dtype='int')
                newdata = resize_array(basedata, finalshape, region)                
                newhdu = pyfits.PrimaryHDU(newdata, hdu.header)                
                _logger.info('Saving %s', maskn)
                newhdu.writeto(maskn)
            finally:
                hdulist.close()
        
            # Create empty object mask             
            objmaskname = _name_object_mask(image.label, 0)
            image.objmask = objmaskname
            _logger.info('Creating %s', objmaskname)
        
        _logger.info('Superflat correction')
        
        _logger.info('SF: computing scale factors')
        for image in images_info:
            region = image.region
            data = pyfits.getdata(image.resized_base)[region]
            mask = pyfits.getdata(image.resized_mask)[region]
            image.median_scale = numpy.median(data[mask == 0])
            _logger.debug('median value of %s is %f', image.resized_base, image.median_scale)
        
        # Combining images to obtain the sky flat
        # Open all images 
        _logger.info("Iter %d, SF: combining the images without offsets", 0)
        fitted = self.compute_superflat(images_info, 0)
        
        _logger.info("Iter %d, SF: apply superflat", 0)
        # Process all images with the fitted flat
        self.correct_superflat(images_info, fitted, 0)
        
        _logger.info('Iter %d, sky correction (SC)', 0)
        _logger.info('Iter %d, SC: computing simple sky', 0)            
        self.compute_simple_sky(images_info, 0)

        # Combining the images
        _logger.info("Iter %d, Combining the images", 0)
        self.combine_images(images_info, 0)

        _logger.info('Iter %d, finished', 0)

     
        result = 1
        return {'qa': numina.qa.UNKNOWN, 'result_image': result}


