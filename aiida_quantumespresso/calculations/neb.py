# -*- coding: utf-8 -*-
"""
Plugin to create a Quantum Espresso neb.x input file.
"""
from __future__ import absolute_import
import os
import copy
import six

from aiida.common import InputValidationError
from aiida.common import CalcInfo, CodeInfo
from aiida.common import LinkType
from aiida.common.lang import classproperty
from aiida import orm
from aiida.engine import CalcJob

from aiida_quantumespresso.calculations import BasePwCpInputGenerator
from aiida_quantumespresso.calculations.pw import PwCalculation
from aiida_quantumespresso.calculations import _lowercase_dict, _uppercase_dict, _pop_parser_options
from aiida_quantumespresso.utils.convert import convert_input_to_namelist_entry


class NebCalculation(CalcJob):
    """
    Nudged Elastic Band code (neb.x) of Quantum ESPRESSO distribution
    For more information, refer to http://www.quantum-espresso.org/
    """
    _PREFIX = 'aiida'

    # in restarts, will not copy but use symlinks
    _default_symlink_usage = False

    # Default input and output file names
    _DEFAULT_INPUT_FILE = 'neb.dat'
    _DEFAULT_OUTPUT_FILE = 'aiida.out'
    _PSEUDO_SUBFOLDER = PwCalculation._PSEUDO_SUBFOLDER
    _OUTPUT_SUBFOLDER = PwCalculation._OUTPUT_SUBFOLDER

    # Keywords that cannot be set (for the PW input)
    _blocked_keywords = []

    _use_kpoints = True

    @classproperty
    def _internal_retrieve_list(cls):
        # I retrieve them all, even if I don't parse all of them
        _neb_ext_list = ['path', 'dat', 'int']
        return [ '{}.{}'.format(cls._PREFIX, ext) for ext in _neb_ext_list]

    @classproperty
    def xml_filepaths(cls):
        """Returns a list of relative filepaths of XML files."""
        filepaths = []

        for filename in PwCalculation.xml_filenames:
            filepath = os.path.join(cls._OUTPUT_SUBFOLDER, cls._PREFIX+'_*[0-9]', cls._PREFIX+'.save', filename)
            filepaths.append(filepath)

        return filepaths

    @classmethod
    def define(cls, spec):
        super(NebCalculation, cls).define(spec)
        spec.input('metadata.options.input_filename', valid_type=six.string_types, default=cls._DEFAULT_INPUT_FILE)
        spec.input('metadata.options.output_filename', valid_type=six.string_types, default=cls._DEFAULT_OUTPUT_FILE)
        spec.input('metadata.options.parser_name', valid_type=six.string_types, default='quantumespresso.neb')
        spec.input('first_structure', valid_type=orm.StructureData, help='Initial structure')
        spec.input('last_structure', valid_type=orm.StructureData, help='Final structure')
        spec.input('parameters', valid_type=orm.Dict, help='NEB-specific input parameters')
        spec.input('settings', valid_type=orm.Dict, required=False,
            help='Optional parameters to affect the way the calculation job and the parsing are performed.')
        spec.input('parent_folder', valid_type=orm.RemoteData, required=False,
            help='An optional working directory of a previously completed calculation to restart from.')
        # We reuse some inputs from PwCalculation to construct the PW-specific parts of the input files
        spec.expose_inputs(PwCalculation, namespace='pw', include=('parameters','pseudos','kpoints','vdw_table'))
        spec.output('output_parameters', valid_type=orm.Dict,
            help='The output parameters dictionary of the NEB calculation')
        spec.output('output_trajectory', valid_type=orm.TrajectoryData)
        spec.output('iteration_array', valid_type=orm.ArrayData, required=False)
        spec.output('output_mep', valid_type=orm.ArrayData,
            help='ArrayData containing the original and interpolated energy profiles along the minimum-energy path (mep)')
        spec.default_output_node = 'output_parameters'
        spec.exit_code(
            100, 'ERROR_NO_RETRIEVED_FOLDER', message='The retrieved folder data node could not be accessed.')
        #spec.exit_code(
        #    101, 'ERROR_NO_RETRIEVED_TEMPORARY_FOLDER', message='The retrieved temporary folder could not be accessed.')
        spec.exit_code(
            110, 'ERROR_READING_OUTPUT_FILE', message='The output file could not be read from the retrieved folder.')
        spec.exit_code(
            115, 'ERROR_MISSING_XML_FILE', message='The required XML file is not present in the retrieved folder.')
        #spec.exit_code(
        #    116, 'ERROR_MULTIPLE_XML_FILES', message='The retrieved folder contains multiple XML files.')
        #spec.exit_code(
        #    117, 'ERROR_READING_XML_FILE', message='The required XML file could not be read.')
        spec.exit_code(
            120, 'ERROR_INVALID_OUTPUT', message='The output file contains invalid output.')
        #spec.exit_code(
        #    130, 'ERROR_JOB_NOT_DONE', message='The computation did not finish properly (\'JOB DONE\' not found).')
        # TODO: check error logic and maybe use these commented-out exit codes
        spec.exit_code(312, 'ERROR_OUTPUT_STDOUT_INCOMPLETE',
            message='The stdout output file was incomplete.')
        spec.exit_code(320, 'ERROR_OUTPUT_XML_READ',
            message='The XML output file could not be read.')
        spec.exit_code(321, 'ERROR_OUTPUT_XML_PARSE',
            message='The XML output file could not be parsed.')
        spec.exit_code(322, 'ERROR_OUTPUT_XML_FORMAT',
            message='The XML output file has an unsupported format.')
        spec.exit_code(350, 'ERROR_UNEXPECTED_PARSER_EXCEPTION',
            message='The parser raised an unexpected exception.')

    @classmethod
    def _generate_NEBinputdata(cls, neb_parameters, settings_dict):
        """
        This methods generate the input data for the NEB part of the calculation
        """
        # I put the first-level keys as uppercase (i.e., namelist and card names)
        # and the second-level keys as lowercase
        # (deeper levels are unchanged)
        input_params = _uppercase_dict(neb_parameters.get_dict(), dict_name='parameters')
        input_params = {k: _lowercase_dict(v, dict_name=k) for k, v in six.iteritems(input_params)}

        # Force default values for blocked keywords. NOTE: this is different from PW/CP
        for blocked in cls._blocked_keywords:
            namelist = blocked[0].upper()
            key = blocked[1].lower()
            value = blocked[2]
            if namelist in input_params:
                if key in input_params[namelist]:
                    raise InputValidationError(
                        "You cannot specify explicitly the '{}' key in the '{}' "
                        'namelist.'.format(key, namelist))
            else:
                input_params[namelist] = {}
            input_params[namelist][key] = value

        # Create an empty dictionary for the compulsory namelist 'PATH' if not present
        if 'PATH' not in input_params:
            input_params['PATH'] = {}

        # In case of climbing image, we need the corresponding card
        ci_scheme = input_params['PATH'].get('ci_scheme', 'no-ci').lower()
        climbing_image_list = settings_dict.pop('CLIMBING_IMAGES', None)
        if ci_scheme == 'manual':
            manual_climbing_image = True
            if climbing_image_list is None:
                raise InputValidationError("'ci_scheme' is {}, but no climbing images were specified for this "
                                           'calculation.'.format(ci_scheme))
            if not isinstance(climbing_image_list, list):
                raise InputValidationError('Climbing images should be provided as a list.')
            num_of_images = input_params['PATH'].get('num_of_images', 2)
            if any([ (i<2 or i>=num_of_images) for i in climbing_image_list ]):
                raise InputValidationError('The climbing images should be in the range between the first '
                                           'and the last image (excluded).')
            climbing_image_card = 'CLIMBING_IMAGES\n'
            climbing_image_card += ', '.join([str(_) for _ in climbing_image_list]) + '\n'
        else:
            manual_climbing_image = False
            if climbing_image_list is not None:
                raise InputValidationError("Climbing images are not accepted when 'ci_scheme' is {}.".format(ci_scheme))

        input_data = u'&PATH\n'
        # namelist content; set to {} if not present, so that we leave an empty namelist
        namelist = input_params.pop('PATH', {})
        for k, v in sorted(six.iteritems(namelist)):
            input_data += convert_input_to_namelist_entry(k, v)
        input_data += u'/\n'

        # Write CI cards now
        if manual_climbing_image:
            input_data += climbing_image_card

        if input_params:
            raise InputValidationError(
                'The following namelists are specified in input_params, but are '
                'not valid namelists for the current type of calculation: '
                '{}'.format(','.join(list(input_params.keys()))))

        return input_data

    def prepare_for_submission(self, folder):
        """Create the input files from the input nodes passed to this instance of the `CalcJob`.

        :param folder: an `aiida.common.folders.Folder` to temporarily write files on disk
        :return: `aiida.common.datastructures.CalcInfo` instance
        """

        import numpy as np

        local_copy_list = []
        remote_copy_list = []
        remote_symlink_list = []

        # Convert settings dictionary to have uppercase keys, or create an empty one if none was given.
        if 'settings' in self.inputs:
            settings_dict = _uppercase_dict(self.inputs.settings.get_dict(), dict_name='settings')
        else:
            settings_dict = {}

        first_structure = self.inputs.first_structure
        last_structure = self.inputs.last_structure

        # Check that the first and last image have the same cell
        if abs(np.array(first_structure.cell)-
               np.array(last_structure.cell)).max() > 1.e-4:
            raise InputValidationError('Different cell in the fist and last image')

        # Check that the first and last image have the same number of sites
        if len(first_structure.sites) != len(last_structure.sites):
            raise InputValidationError('Different number of sites in the fist and last image')

        # Check that sites in the initial and final structure have the same kinds
        if first_structure.get_site_kindnames() != last_structure.get_site_kindnames():
            raise InputValidationError('Mismatch between the kind names and/or order between '
                                       'the first and final image')

        # Check that a pseudo potential was specified for each kind present in the `StructureData`
        # self.inputs.pw.pseudos is a plumpy.utils.AttributesFrozendict
        kindnames = [kind.name for kind in first_structure.kinds]
        if set(kindnames) != set(self.inputs.pw.pseudos.keys()):
            raise InputValidationError(
                'Mismatch between the defined pseudos and the list of kinds of the structure.\n'
                'Pseudos: {};\nKinds: {}'.format(', '.join(list(self.inputs.pw.pseudos.keys())), ', '.join(list(kindnames))))

        ##############################
        # END OF INITIAL INPUT CHECK #
        ##############################

        # Create the subfolder that will contain the pseudopotentials
        folder.get_subfolder(self._PSEUDO_SUBFOLDER, create=True)
        # Create the subfolder for the output data (sometimes Quantum ESPRESSO codes crash if the folder does not exist)
        folder.get_subfolder(self._OUTPUT_SUBFOLDER, create=True)

        # We first prepare the NEB-specific input file.
        neb_input_filecontent = self._generate_NEBinputdata(self.inputs.parameters, settings_dict)
        with folder.open(self.inputs.metadata.options.input_filename, 'w') as handle:
            handle.write(neb_input_filecontent)

        # We now generate the PW input files for each input structure
        local_copy_pseudo_list = []
        for i, structure in enumerate([first_structure, last_structure]):
            # We need to a pass a copy of the settings_dict for each structure
            this_settings_dict = copy.deepcopy(settings_dict)
            pw_input_filecontent, this_local_copy_pseudo_list = PwCalculation._generate_PWCPinputdata(
                self.inputs.pw.parameters, this_settings_dict, self.inputs.pw.pseudos, structure, self.inputs.pw.kpoints
            )
            local_copy_pseudo_list += this_local_copy_pseudo_list
            with folder.open('pw_{}.in'.format(i+1), 'w') as handle:
                handle.write(pw_input_filecontent)

        # We need to pop the settings that were used in the PW calculations
        for key in list(settings_dict.keys()):
            if key not in list(this_settings_dict.keys()):
                settings_dict.pop(key)

        # We avoid to copy twice the same pseudopotential to the same filename
        local_copy_pseudo_list = set(local_copy_pseudo_list)
        # We check that two different pseudopotentials are not copied
        # with the same name (otherwise the first is overwritten)
        if len({ filename for (uuid, filename, local_path) in local_copy_pseudo_list}) < len(local_copy_pseudo_list):
            raise InputValidationError('Same filename for two different pseudopotentials')

        local_copy_list += local_copy_pseudo_list

        # If present, add also the Van der Waals table to the pseudo dir. Note that the name of the table is not checked
        # but should be the one expected by Quantum ESPRESSO.
        vdw_table = self.inputs.get('pw.vdw_table', None)
        if vdw_table:
            local_copy_list.append((
                vdw_table.uuid,
                vdw_table.filename,
                os.path.join(self._PSEUDO_SUBFOLDER, vdw_table.filename)
            ))

        # operations for restart
        parent_calc_folder = self.inputs.get('parent_folder', None)
        symlink = settings_dict.pop('PARENT_FOLDER_SYMLINK', self._default_symlink_usage)  # a boolean
        if symlink:
            if parent_calc_folder is not None:
                # I put the symlink to the old parent ./out folder
                remote_symlink_list.append((
                    parent_calc_folder.computer.uuid,
                    os.path.join(parent_calc_folder.get_remote_path(),
                                 self._OUTPUT_SUBFOLDER, '*'),  # asterisk: make individual symlinks for each file
                    self._OUTPUT_SUBFOLDER
                ))
                # and to the old parent prefix.path
                remote_symlink_list.append((
                    parent_calc_folder.computer.uuid,
                    os.path.join(parent_calc_folder.get_remote_path(),
                                 '{}.path'.format(self._PREFIX)),
                    '{}.path'.format(self._PREFIX)
                ))
        else:
            # copy remote output dir and .path file, if specified
            if parent_calc_folder is not None:
                remote_copy_list.append((
                    parent_calc_folder.computer.uuid,
                    os.path.join(parent_calc_folder.get_remote_path(),
                                 self._OUTPUT_SUBFOLDER, '*'),
                    self._OUTPUT_SUBFOLDER
                ))
                # and copy the old parent prefix.path
                remote_copy_list.append((
                    parent_calc_folder.computer.uuid,
                    os.path.join(parent_calc_folder.get_remote_path(),
                                 '{}.path'.format(self._PREFIX)),
                    '{}.path'.format(self._PREFIX)
                ))

        # here we may create an aiida.EXIT file
        create_exit_file = settings_dict.pop('ONLY_INITIALIZATION', False)
        if create_exit_file:
            exit_filename = '{}.EXIT'.format(self._PREFIX)
            with folder.open(exit_filename, 'w') as f:
                f.write('\n')

        calcinfo = CalcInfo()
        codeinfo = CodeInfo()

        calcinfo.uuid = self.uuid
        # Empty command line by default
        cmdline_params = settings_dict.pop('CMDLINE', [])
        # For the time-being we only have the initial and final image
        calcinfo.local_copy_list = local_copy_list
        calcinfo.remote_copy_list = remote_copy_list
        calcinfo.remote_symlink_list = remote_symlink_list
        # In neb calculations there is no input read from standard input!!

        codeinfo.cmdline_params = (['-input_images', '2']
                                   + list(cmdline_params))
        codeinfo.stdout_name = self.inputs.metadata.options.output_filename
        codeinfo.code_uuid = self.inputs.code.uuid
        calcinfo.codes_info = [codeinfo]

        # Retrieve the output files and the xml files
        calcinfo.retrieve_list = []
        calcinfo.retrieve_list.append(self.inputs.metadata.options.output_filename)
        calcinfo.retrieve_list.append((
            os.path.join(self._OUTPUT_SUBFOLDER, self._PREFIX + '_*[0-9]', 'PW.out'),  # source relative path (globbing)
            '.',  # destination relative path
            2  # depth to preserve
        ))

        for xml_filepath in self.xml_filepaths:
            calcinfo.retrieve_list.append([xml_filepath, '.', 3])

        calcinfo.retrieve_list += settings_dict.pop('ADDITIONAL_RETRIEVE_LIST', [])
        calcinfo.retrieve_list += self._internal_retrieve_list

        # We might still have parser options in the settings dictionary: pop them.
        _pop_parser_options(self, settings_dict)

        if settings_dict:
            unknown_keys = ', '.join(list(settings_dict.keys()))
            raise InputValidationError('`settings` contained unexpected keys: {}'.format(unknown_keys))

        return calcinfo

