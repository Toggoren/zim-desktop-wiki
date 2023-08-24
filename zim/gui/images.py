# Copyright 2008-2021 Jaap Karssenberg <jaap.karssenberg@gmail.com>

"""This module contains the image processing functions
"""

import logging
from typing import Optional

from gi.repository import GLib
from gi.repository import GdkPixbuf

from zim.newfs import LocalFile

try:
	from PIL import Image, UnidentifiedImageError, __version__ as PILLOW_VERSION_STRING
except ImportError:
	PILLOW_AVAILABLE = False
else:
	PILLOW_AVAILABLE = True


logger = logging.getLogger('zim.gui.images')


def image_file_get_dimensions(file_path):
	"""
	Replacement for GdkPixbuf.Pixbuf.get_file_info
	@return (width, height) in pixels
		or None if file does not exist or failed to load
	"""

	# Let GTK try reading the file
	_, width, height = GdkPixbuf.Pixbuf.get_file_info(file_path)
	if width > 0 and height > 0:
		return (width, height)

	if not PILLOW_AVAILABLE:
		raise AssertionError('Could not get size for: %s' % file_path)

	# Fallback to Pillow
	with Image.open(file_path) as img_pil:
		return (img_pil.width, img_pil.height)


def _convert_pillow_image_to_pixbuf(image: 'Image.Image') -> GdkPixbuf.Pixbuf:
	# check if there is an alpha channel
	if image.mode == 'RGB':
		has_alpha = False
	elif image.mode == 'RGBA':
		has_alpha = True
	else:
		raise ValueError(f'Pixel format {image.mode} can not be converted to Pixbuf for image {image}')

	# convert to GTK pixbuf
	data_gtk = GLib.Bytes.new_take(image.tobytes())

	return GdkPixbuf.Pixbuf.new_from_bytes(
		data=data_gtk,
		colorspace=GdkPixbuf.Colorspace.RGB,
		has_alpha=has_alpha,
		# GTK docs: "Currently only RGB images with 8 bits per sample are supported"
		# https://docs.gtk.org/gdk-pixbuf/ctor.Pixbuf.new_from_bytes.html#description
		bits_per_sample=8,
		width=image.width,
		height=image.height,
		rowstride=image.width * (4 if has_alpha else 3),
	)


def _extract_orientation_using_pillow(image: 'Image.Image') -> Optional[int]:
	pillow_version = tuple(map(int, PILLOW_VERSION_STRING.split('.')))
	if pillow_version >= (6, 0, 0):
		# https://pillow.readthedocs.io/en/stable/releasenotes/6.0.0.html#added-exif-class
		exif = image.getexif()
	else:
		# noinspection PyUnresolvedReferences,PyProtectedMember
		exif = image._getexif()  # noqa: WPS437
		if not exif:
			exif = {}  # noqa: WPS437
	orientation_tag_id = 274
	orientation = exif.get(orientation_tag_id)
	return int(orientation) if orientation else None


def image_file_load_pixels(file: LocalFile, requested_width: int = -1, requested_height: int = -1) -> GdkPixbuf:
	"""
	Replacement for GdkPixbuf.Pixbuf.new_from_file_at_size(file.path, w, h)
	When file does not exist or fails to load, this throws exceptions.
	"""

	if not file.exists():
		# if the file does not exist, no need to make the effort of trying to read it
		raise FileNotFoundError(file.path)

	need_switch_to_fallback = True
	try:
		pixbuf = GdkPixbuf.Pixbuf.new_from_file(file.path)
	except GLib.GError:
		logger.debug(f'GTK failed to read image, let\'s try fallbacks: {file.path}')
	else:
		need_switch_to_fallback = False

	# save the ref to avoid re-reading when retrieving the orientation tag
	pillow_image: Optional['Image.Image'] = None
	if need_switch_to_fallback:
		if PILLOW_AVAILABLE:
			logger.debug(f'Try using Pillow fallback: {file.path}')
			try:
				with Image.open(file.path) as image:
					pillow_image = image
					pixbuf = _convert_pillow_image_to_pixbuf(image)
			except UnidentifiedImageError:
				logger.debug(f'Pillow failed to read image: {file.path}')
			else:
				need_switch_to_fallback = False

	if need_switch_to_fallback:
		raise RuntimeWarning(f'No available fallbacks for load this image: {file.path}')

	# Let's try to find and remember the orientation before scaling,
	# 	because we lose metadata when changing images.
	orientation: Optional[int] = None
	mimetype = file.mimetype()
	if mimetype in {'image/jpeg', 'image/tiff'}:
		# Gtk can detect orientation in jpeg|tiff images only
		# See docs: https://docs.gtk.org/gdk-pixbuf/method.Pixbuf.get_option.html#description
		orientation = pixbuf.get_option('orientation')
	if mimetype in {'image/webp', 'image/png'}:
		# if possible, we will find orientation of the image using Pillow,
		# 	if it is not available, we will display image it as is.
		if PILLOW_AVAILABLE:
			if pillow_image is None:
				try:
					with Image.open(file.path) as image:
						orientation = _extract_orientation_using_pillow(image)
				except UnidentifiedImageError:
					logger.debug(f'Pillow failed to read orientation tag from image: {file.path}')
			else:
				orientation = _extract_orientation_using_pillow(pillow_image)

	if orientation is None:
		msg = f'No orientation tag was found in image {file}.'
	else:
		msg = f'The orientation tag "{orientation}" was found in the {file} image.'
	logger.debug(msg)

	need_scale = requested_width > 0 or requested_height > 0
	if need_scale:
		width, height = pixbuf.get_width(), pixbuf.get_height()
		need_swap_width_and_height = orientation in {5, 6, 7, 8}
		if need_swap_width_and_height:
			width, height = height, width
		if requested_height <= 0:
			requested_height = int(height * requested_width / width)
		else:
			requested_width = int(width * requested_height / height)
		if need_swap_width_and_height:
			requested_width, requested_height = requested_height, requested_width

		# do not use new_from_file_at_size() here due to bug in Gtk for GIF images, see issue #1563
		pixbuf = pixbuf.scale_simple(requested_width, requested_height, GdkPixbuf.InterpType.BILINEAR)

	if orientation is not {None, 1}:
		pixbuf.set_option('orientation', f'{orientation}')
		pixbuf = GdkPixbuf.Pixbuf.apply_embedded_orientation(pixbuf)

	return pixbuf
