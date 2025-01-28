
# Copyright 2013-2024 Jaap Karssenberg <jaap.karssenberg@gmail.com>

'''This module defines the L{main()} function for executing the zim
application. It also defines a number of command classes that implement
specific commandline commands and an singleton application object that
takes core of the process life cycle.
'''

import logging

logger = logging.getLogger('zim')

from contextlib import contextmanager

import zim
import zim.newfs
import zim.config

from zim import __version__

from zim.base.klasslookup import get_module, lookup_subclass
from zim.errors import Error
from zim.notebook import Path, HRef, \
	get_notebook_list, resolve_notebook, build_notebook
from zim.formats import get_format

from zim.config import ConfigManager
from zim.plugins import PluginManager

from .command import Command, GtkCommand, UsageError, GetoptError
	# Keep GetOptError import - not used here, but imported from here by zim.py


class HelpCommand(Command):
	'''Class implementing the C{--help} command'''

	usagehelp = '''\
usage: zim [OPTIONS] [NOTEBOOK [PAGE_LINK]]
   or: zim --gui [OPTIONS] [NOTEBOOK [PAGE_LINK]]
   or: zim --server [OPTIONS] [NOTEBOOK]
   or: zim --export [OPTIONS] NOTEBOOK [PAGE]
   or: zim --import [OPTIONS] NOTEBOOK PAGE FILES
   or: zim --search [OPTIONS] NOTEBOOK QUERY
   or: zim --index  [OPTIONS] NOTEBOOK
   or: zim --plugin PLUGIN [ARGUMENTS]
   or: zim --manual [OPTIONS] [PAGE_LINK]
   or: zim --help

NOTEBOOK can be a local file path, a local file URI or a notebook name
PAGE is be a fully specified page name
PAGE_LINK is a fully specified page name optionally extended with an anchor ID
'''
	optionhelp = '''\
General Options:
  --gui             run the editor (this is the default)
  --server          run the web server
  --export          export to a different format
  --import          import one or more files into a notebook
  --search          run a search query on a notebook
  --index           build an index for a notebook
  --plugin          call a specific plugin function
  --manual          open the user manual
  -V, --verbose     print information to terminal
  -D, --debug       print debug messages
  -v, --version     print version and exit
  -h, --help        print this text

GUI Options:
  --list            show the list with notebooks instead of
                    opening the default notebook
  --geometry        window size and position as WxH+X+Y
  --fullscreen      start in fullscreen mode
  --non-unique      start a new process, do not connect to an existing process
  --standalone      start a new process per notebook, implies --non-unique

Server Options:
  --port            port to use (defaults to 8080)
  --template        name or filepath of the template to use
  --private         serve only to localhost
  --gui             run the gui wrapper for the server

Export Options:
  -o, --output      output directory (mandatory option)
  --format          format to use (defaults to 'html')
  --template        name or filepath of the template to use
  --root-url        url to use for the document root
  --index-page      index page name
  -r, --recursive   when exporting a page, also export sub-pages
  -s, --singlefile  export all pages to a single output file
  -O, --overwrite   force overwriting existing file(s)

Import Options:
  --format          format to read (defaults to 'wiki')
  --assubpage       import files as sub-pages of PATH, this is implicit true
                    when PATH ends with a ":" or when multiple files are given

Search Options:
  -s, --with-scores print score for each page, sort by score

Index Options:
  -f, --flush       flush the index first and force re-building

Try 'zim --manual' for more help.
'''

	def __init__(self, command, cmdhelp=None, pwd=None):
		Command.__init__(self, command, pwd=None)
		self.cmdhelp = cmdhelp

	def run(self):
		if self.cmdhelp:
			print(self.cmdhelp)
		else:
			print(self.usagehelp)
			print(self.optionhelp)  # TODO - generate from commands


class VersionCommand(Command):
	'''Class implementing the C{--version} command'''

	def run(self):
		print('zim %s\n' % zim.__version__)
		print(zim.__copyright__, '\n')
		print(zim.__license__)


class NotebookLookupError(Error):
	'''Error when failing to locate a notebook'''

	description = _('Could not find the file or folder for this notebook')
		# T: Error verbose description


class NotebookCommand(Command):
	'''Base class for commands that act on a notebook'''

	def get_default_or_only_notebook(self):
		'''Helper to get a default notebook'''
		notebooks = get_notebook_list()
		if notebooks.default:
			uri = notebooks.default.uri
		elif len(notebooks) == 1:
			uri = notebooks[0].uri
		else:
			return None

		return resolve_notebook(uri, pwd=self.pwd) # None if not found

	def get_notebook_argument(self):
		'''Get the notebook and page arguments for this command
		@returns: a 2-tuple of an L{NotebookInfo} object and an
		optional L{HRef} or C{(None, None)} if the notebook
		argument is optional and not given
		@raises NotebookLookupError: if the notebook is mandatory and
		not given, or if it is given but could not be resolved
		'''
		assert self.arguments[0] in ('NOTEBOOK', '[NOTEBOOK]')
		args = self.get_arguments()
		notebook = args[0]

		if notebook is None:
			if self.arguments[0] == 'NOTEBOOK': # not optional
				raise NotebookLookupError(_('Please specify a notebook'))
					# T: Error when looking up a notebook
			else:
				return None, None

		notebookinfo = resolve_notebook(notebook, pwd=self.pwd)
		if not notebookinfo:
			raise NotebookLookupError(_('Could not find notebook: %s') % notebook)
				# T: error message

		if len(self.arguments) > 1 \
		and self.arguments[1] in ('PAGE', '[PAGE]', 'PAGE_LINK', '[PAGE_LINK]') \
		and args[1] is not None and args[1] != ':':
			# The ":" path is a special case to allow e.g. --import to handle top level namespace
			return notebookinfo, HRef.new_from_wiki_link(args[1])
		else:
			return notebookinfo, None

	def build_notebook(self, ensure_uptodate=True):
		'''Get the L{Notebook} object for this command
		Tries to automount the file location if needed.
		@param ensure_uptodate: if C{True} index is updated when needed.
		Only set to C{False} when index update is handled explicitly
		(e.g. in the main gui).
		@returns: a L{Notebook} object and a C{HRef} object or C{None}
		@raises NotebookLookupError: if the notebook could not be
		resolved or is not given
		@raises FileNotFoundError: if the notebook location does not
		exist and could not be mounted.
		'''
		# Explicit page argument has priority over implicit from uri
		# mounting is attempted by zim.notebook.build_notebook()
		notebookinfo, pagelink = self.get_notebook_argument() 	# can raise NotebookLookupError
		if not notebookinfo:
			raise NotebookLookupError(_('Please specify a notebook'))
		notebook, uripagelink = build_notebook(notebookinfo) # can raise FileNotFoundError

		if ensure_uptodate and not notebook.index.is_uptodate:
			for info in notebook.index.update_iter():
				#logger.info('Indexing %s', info)
				pass # TODO meaningful info for above message

		return notebook, pagelink or uripagelink


class GuiCommand(NotebookCommand, GtkCommand):
	'''Class implementing the C{--gui} command and run the gtk interface'''

	arguments = ('[NOTEBOOK]', '[PAGE_LINK]')
	options = (
		('list', '', 'show the list with notebooks instead of\nopening the default notebook'),
		('geometry=', '', 'window size and position as WxH+X+Y'),
		('fullscreen', '', 'start in fullscreen mode'),
		('non-unique', '', 'start a new process, do not connect to an existing process'),
		('standalone', '', 'start a new process per notebook, implies --non-unique'),
	)

	def build_notebook(self, ensure_uptodate=False):
		# Bit more complicated here due to options to use default and
		# allow using notebookdialog to prompt

		# Explicit page argument has priority over implicit from uri
		# mounting is attempted by zim.notebook.build_notebook()

		from zim.notebook import FileNotFoundError

		def prompt_notebook_list():
			import zim.gui.notebookdialog
			return zim.gui.notebookdialog.prompt_notebook()
				# Can return None if dialog is cancelled

		used_default = False
		pagelink, uripagelink = None, None
		if self.opts.get('list'):
			notebookinfo = prompt_notebook_list()
		else:
			notebookinfo, pagelink = self.get_notebook_argument()

			if notebookinfo is None:
				notebookinfo = self.get_default_or_only_notebook()
				used_default = notebookinfo is not None

				if notebookinfo is None:
					notebookinfo = prompt_notebook_list()

		if notebookinfo is None:
			return None, None # Cancelled prompt

		try:
			notebook, uripagelink = build_notebook(notebookinfo) # can raise FileNotFound
		except FileNotFoundError:
			if used_default:
				# Default notebook went missing? Fallback to dialog to allow changing it
				notebookinfo = prompt_notebook_list()
				if notebookinfo is None:
					return None, None # Cancelled prompt
				notebook, uripagelink = build_notebook(notebookinfo) # can raise FileNotFound
			else:
				raise

		if ensure_uptodate and not notebook.index.is_uptodate:
			for info in notebook.index.update_iter():
				#logger.info('Indexing %s', info)
				pass # TODO meaningful info for above message

		return notebook, pagelink or uripagelink

	def run(self):
		from gi.repository import Gtk

		from zim.gui.mainwindow import MainWindow

		windows = [
			w for w in Gtk.Window.list_toplevels()
				if isinstance(w, MainWindow)
		]

		notebook, pagelink = self.build_notebook()
		if notebook is None:
			logger.debug('NotebookDialog cancelled - exit')
			return

		for window in windows:
			if window.notebook.uri == notebook.uri:
				self._present_window(window, pagelink)
				return window
		else:
			return self._run_new_window(notebook, pagelink)

	def _present_window(self, window, pagelink):
		window.present()

		if pagelink:
			window.open_page(Path(pagelink.names), anchor=pagelink.anchor)

		geometry = self.opts.get('geometry', None)
		if geometry is not None:
			window.parse_geometry(geometry)

		if self.opts.get('fullscreen', False):
			window.toggle_fullscreen(True)

	def _run_new_window(self, notebook, pagelink):
		from gi.repository import GObject

		from zim.gui.mainwindow import MainWindow

		pluginmanager = PluginManager()

		preferences = ConfigManager.preferences['General']
		preferences.setdefault('plugins_list_version', 'none')
		if preferences['plugins_list_version'] != '0.70':
			if not preferences['plugins']:
				pluginmanager.load_plugins_from_preferences(
					[ # Default plugins
						'pageindex', 'pathbar', 'toolbar',
						'insertsymbol', 'printtobrowser',
						'versioncontrol', 'osx_menubar'
					]
				)
			else:
				# Upgrade version <0.70 where these were core functions
				pluginmanager.load_plugins_from_preferences(['pageindex', 'pathbar'])

			if 'calendar' in pluginmanager.failed:
				ConfigManager.preferences['JournalPlugin'] = \
						ConfigManager.preferences['CalendarPlugin']
				pluginmanager.load_plugins_from_preferences(['journal'])

			preferences['plugins_list_version'] = '0.70'

		page = Path(pagelink.names) if pagelink else None
		window = MainWindow(
			notebook,
			page=page,
			**self.get_options('geometry', 'fullscreen')
		)
		window.present()
		if pagelink and pagelink.anchor:
			window.open_page(Path(pagelink.names), anchor=pagelink.anchor)

		if not window.notebook.index.is_uptodate:
			window._uiactions.check_and_update_index(update_only=True) # XXX
		else:
			# Start a lightweight background check of the index
			# put a small delay to ensure window is shown before we start
			def start_background_check():
				notebook.index.start_background_check(notebook)
				return False # only run once
			GObject.timeout_add(500, start_background_check)

		return window


class ManualCommand(GuiCommand):
	'''Like L{GuiCommand} but always opens the manual'''

	arguments = ('[PAGE_LINK]',)
	options = tuple(t for t in GuiCommand.options if t[0] != 'list')
		# exclude --list

	def run(self):
		from zim.config import data_dir
		self.arguments = ('NOTEBOOK', '[PAGE_LINK]') # HACK
		self.args.insert(0, data_dir('manual').path)
		return GuiCommand.run(self)


class ServerCommand(NotebookCommand):
	'''Class implementing the C{--server} command and running the web
	server.
	'''

	arguments = ('NOTEBOOK',)
	options = (
		('port=', 'p', 'port number to use (defaults to 8080)'),
		('template=', 't', 'name or path of the template to use'),
		('private', '', 'serve only to localhost')
	)

	def run(self):
		import zim.www
		from zim.templates import get_template

		port = int(self.opts.get('port', 8080))
		template = get_template('html', self.opts.get('template', 'Default'), pwd=self.pwd)
		notebook, x = self.build_notebook()
		is_public = not self.opts.get('private', False)

		self.server = httpd = zim.www.make_server(notebook, public=is_public, template=template, port=port)
			# server attribute used in testing to stop sever in thread
		logger.info("Serving HTTP on %s port %i...", httpd.server_name, httpd.server_port)
		httpd.serve_forever()


class ServerGuiCommand(NotebookCommand, GtkCommand):
	'''Like L{ServerCommand} but uses the graphical interface for the
	server defined in L{zim.gui.server}.
	'''

	arguments = ('[NOTEBOOK]',)
	options = (
		('port=', 'p', 'port number to use (defaults to 8080)'),
		('template=', 't', 'name or path of the template to use'),
		('non-unique', '', 'start a new process, do not connect to an existing process'),
	)

	def run(self):
		import zim.gui.server
		from zim.templates import get_template

		port = int(self.opts.get('port', 8080))
		template = get_template('html', self.opts.get('template', 'Default'), pwd=self.pwd)
		notebookinfo, x = self.get_notebook_argument()
		if notebookinfo is None:
			# Prefer default to be selected in drop down, user can still change
			notebookinfo = self.get_default_or_only_notebook()

		window = zim.gui.server.ServerWindow(
			notebookinfo,
			public=True,
			template=template,
			port=port
		)
		window.show_all()
		return window


class ExportCommand(NotebookCommand):
	'''Class implementing the C{--export} command'''

	arguments = ('NOTEBOOK', '[PAGE]')
	options = (
		('format=', '', 'format to use (defaults to \'html\')'),
		('template=', '', 'name or path of the template to use'),
		('output=', 'o', 'output folder, or output file name'),
		('root-url=', '', 'url to use for the document root'),
		('index-page=', '', 'index page name'),
		('recursive', 'r', 'when exporting a page, also export sub-pages'),
		('singlefile', 's', 'export all pages to a single output file'),
		('overwrite', 'O', 'overwrite existing file(s)'),
	)

	def get_exporter(self, page):
		from zim.newfs import localFileOrFolder, FilePath, LocalFile, LocalFolder, FileNotFoundError
		from zim.templates import get_template
		from zim.export import \
			build_mhtml_file_exporter, \
			build_single_file_exporter, \
			build_page_exporter, \
			build_notebook_exporter

		format = self.opts.get('format', 'html')
		template = get_template(format, self.opts.get('template', 'Default'), pwd=self.pwd)

		if not 'output' in self.opts:
			raise UsageError(_('Output location needed for export')) # T: error in export command

		try:
			output = localFileOrFolder(self.opts['output'], pwd=self.pwd)
		except FileNotFoundError:
			# resolve path, but type undecided
			output = FilePath(self.pwd).get_abspath(self.opts['output']) # can raise again for mal-formed paths
		else:
			# file or folder exists
			if not self.opts.get('overwrite'):
				if isinstance(output, LocalFolder):
					if len(output.list_names()) > 0:
						raise Error(_('Output folder exists and not empty, specify "--overwrite" to force export'))  # T: error message for export
					else:
						pass
				else:
					raise Error(_('Output file exists, specify "--overwrite" to force export'))  # T: error message for export

		if format == 'mhtml':
			self.ignore_options('index-page')
			if isinstance(output, LocalFolder): # implies exists
				raise UsageError(_('Need output file to export MHTML')) # T: error message for export
			else:
				output = LocalFile(output) # FilePath --> LocalFile

			exporter = build_mhtml_file_exporter(
				output, template,
				document_root_url=self.opts.get('root-url'),
			)
		elif self.opts.get('singlefile'):
			self.ignore_options('index-page')
			if isinstance(output, LocalFolder):
				ext = get_format(format).info['extension']
				output = output.file(page.basename) + '.' + ext
			else:
				output = LocalFile(output) # FilePath --> LocalFile

			exporter = build_single_file_exporter(
				output, format, template, namespace=page,
				document_root_url=self.opts.get('root-url'),
			)
		elif page:
			self.ignore_options('index-page')
			if isinstance(output, LocalFolder):
				ext = get_format(format).info['extension']
				output = output.file(page.basename) + '.' + ext
			else:
				output = LocalFile(output) # FilePath --> LocalFile

			exporter = build_page_exporter(
				output, format, template, page,
				document_root_url=self.opts.get('root-url'),
			)
		else:
			if isinstance(output, LocalFile): # implies exists
				raise UsageError(_('Need output folder to export full notebook')) # T: error message for export
			else:
				output = LocalFolder(output) # FilePath --> LocalFolder

			exporter = build_notebook_exporter(
				output, format, template,
				index_page=self.opts.get('index-page'),
				document_root_url=self.opts.get('root-url'),
			)

		return exporter

	def run(self):
		from zim.export.selections import AllPages, SinglePage, SubPages

		notebook, href = self.build_notebook()

		if href and self.opts.get('recursive'):
			page = Path(href.names) # ignore anchor
			selection = SubPages(notebook, page)
		elif href:
			page = Path(href.names) # ignore anchor
			selection = SinglePage(notebook, page)
		else:
			page = None
			selection = AllPages(notebook)

		exporter = self.get_exporter(page)
		exporter.export(selection)


class ImportCommand(NotebookCommand):
	'''Class implementing the C{--import} command'''

	arguments = ('NOTEBOOK', 'PAGE', 'FILES+')
	options = (
		('format=', '', 'format to import from (defaults to \'wiki\')'),
		('assubpage', 's', 'import as sub-pages of PATH'),
	)

	def run(self):
		from zim.newfs import localFileOrFolder
		from zim.import_files import import_file_from_user_input, import_files_from_user_input

		notebook, href = self.build_notebook()
		path = Path(href.names) if href else None
		format = self.opts.get('format', 'wiki')
		assubpage = self.opts.get('assubpage', False)

		n, p, *files = self.get_arguments()
		files = [localFileOrFolder(f, pwd=self.pwd) for f in files] # raises if does not exist

		if p.endswith(':') or len(files) > 1:
			# Implicit set option, only real use of the option is to force single file imports
			# as sub-page instead of target page
			assubpage = True

		if not assubpage:
			# Special case for 1 file to 1 page
			if notebook.get_page(path).exists():
				raise UsageError('Page "%s" exists, to import as sub-page please use "--assubpage"' % path.name)
			import_file_from_user_input(files[0], notebook, path, format)
		else:
			import_files_from_user_input(files, notebook, path, format)


class SearchCommand(NotebookCommand):
	'''Class implementing the C{--search} command'''

	arguments = ('NOTEBOOK', 'QUERY')
	options = (
		("with-scores", "s", "also print scores of search results"),
	)

	def run(self):
		from zim.search import SearchSelection, Query

		notebook, x = self.build_notebook()
		n, query = self.get_arguments()

		if query and not query.isspace():
			logger.info('Searching for: %s', query)
			query = Query(query)
		else:
			raise ValueError('Empty query')

		selection = SearchSelection(notebook)
		selection.search(query)

		if self.opts.get("with-scores", False):
			sorted_sel = sorted(selection.scores.items(),
				key=lambda i:i[0].name, reverse=False)
			sorted_sel.sort(key=lambda i:i[1], reverse=True)

			for result in sorted_sel:
				print(str(result[1]) + "\t" + result[0].name)
		else:
			for path in sorted(selection, key=lambda p: p.name):
				print(path.name)


class IndexCommand(NotebookCommand):
	'''Class implementing the C{--index} command'''

	arguments = ('NOTEBOOK',)
	options = (
		('flush', 'f', 'flush the index first and force re-building'),
	)

	def run(self):
		# Elevate logging level of indexer to ensure "zim --index -V" gives
		# some meaningfull output
		def elevate_index_logging(log_record):
			if log_record.levelno == logging.DEBUG:
				log_record.levelno = logging.INFO
				log_record.levelname = 'INFO'
			return True

		mylogger = logging.getLogger('zim.notebook.index')
		mylogger.setLevel(logging.DEBUG)
		mylogger.addFilter(elevate_index_logging)

		notebook, x = self.build_notebook(ensure_uptodate=False)
		if self.opts.get('flush'):
			notebook.index.flush()
			notebook.index.update()
		else:
			# Effectively the same as check_and_update_index ui action
			logger.info('Checking notebook index')
			notebook.index.check_and_update()

		logger.info('Index up to date!')


commands = {
	'help': HelpCommand,
	'version': VersionCommand,
	'gui': GuiCommand,
	'manual': ManualCommand,
	'server': ServerCommand,
	'servergui': ServerGuiCommand,
	'export': ExportCommand,
	'import': ImportCommand,
	'search': SearchCommand,
	'index': IndexCommand,
}


def build_command(args, pwd=None):
	'''Parse all commandline options
	@param args: commandline argumnets, starting with first switch or option
	@param pwd: working directory
	@returns: a L{Command} object
	@raises UsageError: if args is not correct
	'''
	args = list(args)
	if args and args[0] == '--plugin':
		args.pop(0)
		try:
			cmd = args.pop(0)
		except IndexError:
			raise UsageError('Missing plugin name')

		try:
			mod = get_module('zim.plugins.' + cmd)
			klass = lookup_subclass(mod, Command)
		except:
			if '-D' in args or '--debug' in args:
				logger.exception('Error while loading: zim.plugins.%s.Command', cmd)
				# Can't use following because log level not yet set:
				# logger.debug('Error while loading: zim.plugins.%s.Command', cmd, exc_info=sys.exc_info())
			raise UsageError('Could not load commandline command for plugin "%s"' % cmd)
		if klass is None:
			raise UsageError('Module %s has no commandline command' % cmd)
	else:
		if args and args[0].startswith('--') and args[0][2:] in commands:
			cmd = args.pop(0)[2:]
			if cmd == 'server' and '--gui' in args:
				args.remove('--gui')
				cmd = 'servergui'
		elif args and args[0] == '-v':
			args.pop(0)
			cmd = 'version'
		elif args and args[0] == '-h':
			args.pop(0)
			cmd = 'help'
		else:
			cmd = 'gui' # default

		klass = commands[cmd]

	obj = klass(cmd, pwd=pwd)
	obj.parse_options(*args)

	# Hack to support --help for plugin commands in local process
	# convert to HelpCommand
	if obj.opts.get('help'):
		obj = HelpCommand('help', cmdhelp=obj.cmdhelp, pwd=pwd)
		obj.parse_options(*args)

	return obj


def _application_startup():
	# Common startup between Gtk application, and non-Gtk commands
	
	## Log application info at startup
	logger.info('This is zim %s', __version__)
	level = logger.getEffectiveLevel()
	if level == logging.DEBUG:
		import sys
		import os
		import zim.config

		logger.debug('Python version is %s', str(sys.version_info))
		logger.debug('Platform is %s', os.name)
		zim.config.log_basedirs()

	## Load plugins before any extendable objects are loaded
	PluginManager().load_plugins_from_preferences(
		ConfigManager.preferences['General']['plugins']
	)


_zim_gtk_application = None


def main(*argv):
	'''Run full zim application
	@param argv: the commandline arguments, as given by C{sys.argv}
	@returns: exit code (if error handled in application process, else just raises)
	'''
	
	# See the `main()`` function in the `zim.py` script for bootstrapping the 
	# environment before running the application.

	# The logic here is:
	# 1. Parse the commandline to determine which Command to run
	# 2. If the Command is _not_ a GtkCommand, setup logging and run the Command
	# 3. If the Command is a GtkCommand, start the application and pass on the standalone / non-unique flag
	# 4. In the application parse the commandline again to re-build the Command in the primary process and run it

	global _zim_gtk_application

	cmd = build_command(argv[1:])
	if isinstance(cmd, GtkCommand):
		from .application import ZimGtkApplication
		_zim_gtk_application = ZimGtkApplication(
			non_unique=cmd.opts.get('non-unique'),
			standalone=cmd.opts.get('standalone')
		)
		argv = [argv[0]] + list(cmd.handle_local_commandline(list(argv[1:])))
		return _zim_gtk_application.run(argv)
	else:
		_application_startup()
		return cmd.run()
