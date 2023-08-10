# Copyright 2008-2021 Jaap Karssenberg <jaap.karssenberg@gmail.com>

"""This module contains the image processing functions
"""

import logging

from gi.repository import GLib
from gi.repository import GdkPixbuf

try:
	from PIL import Image
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


def _convert_pillow_image_to_pixbuf(image: Image.Image) -> GdkPixbuf.Pixbuf:
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


def image_file_load_pixels(file, width_override=-1, height_override=-1):
	"""
	Replacement for GdkPixbuf.Pixbuf.new_from_file_at_size(file.path, w, h)
	When file does not exist or fails to load, this throws exceptions.
	"""

	if not file.exists():
		# if the file does not exist, no need to make the effort of trying to read it
		raise FileNotFoundError(file.path)

	b_size_override = width_override > 0 or height_override > 0
	if b_size_override and (width_override <= 0 or height_override <= 0):
		w, h = image_file_get_dimensions(file.path) # can raise
		if height_override <= 0:
			height_override = int(h * width_override / w)
		else:
			width_override = int(w * height_override / h)

	# Let GTK try reading the file
	try:
		pixbuf = GdkPixbuf.Pixbuf.new_from_file(file.path)

		if b_size_override:
			pixbuf = pixbuf.scale_simple(width_override, height_override, GdkPixbuf.InterpType.BILINEAR)
				# do not use new_from_file_at_size() here due to bug in Gtk for GIF images, see issue #1563

		pixbuf = GdkPixbuf.Pixbuf.apply_embedded_orientation(pixbuf)

	except:
		logger.debug('GTK failed to read image, using Pillow fallback: %s', file.path)

		if not PILLOW_AVAILABLE:
			raise RuntimeWarning('Cannot use Pillow because is not installed.')

		with Image.open(file.path) as img_pil:

			pixbuf = _convert_pillow_image_to_pixbuf(img_pil)

			# resize if a specific size was requested
			if b_size_override:
				pixbuf = pixbuf.scale_simple(width_override, height_override, GdkPixbuf.InterpType.BILINEAR)
					# do not use new_from_file_at_size() here due to bug in Gtk for GIF images, see issue #1563

	return pixbuf
