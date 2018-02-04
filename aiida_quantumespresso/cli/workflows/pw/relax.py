# -*- coding: utf-8 -*-
import click
from aiida_quantumespresso.utils.click import command
from aiida_quantumespresso.utils.click import options


@command()
@options.code()
@options.structure()
@options.pseudo_family()
@options.kpoint_mesh()
@options.max_num_machines()
@options.max_wallclock_seconds()
@options.automatic_parallelization()
@options.clean_workdir()
@options.daemon()
@click.option(
    '-f', '--final-scf', is_flag=True, default=False, show_default=True,
    help='run a final scf calculation for the final relaxed structure'
)
@click.option(
    '-g', '--group', type=click.STRING, required=False,
    help='the label of a Group to add the final PwCalculation to in case of success'
)
def launch(
    code, structure, pseudo_family, kpoints, max_num_machines, max_wallclock_seconds,
    automatic_parallelization, clean_workdir, final_scf, group, daemon):
    """
    Run the PwRelaxWorkChain for a given input structure
    """
    from aiida.orm.data.base import Bool, Str
    from aiida.orm.data.parameter import ParameterData
    from aiida.orm.utils import WorkflowFactory
    from aiida.work.launch import run, submit
    from aiida_quantumespresso.utils.resources import get_default_options

    PwRelaxWorkChain = WorkflowFactory('quantumespresso.pw.relax')

    parameters = {
        'SYSTEM': {
            'ecutwfc': 30.,
            'ecutrho': 240.,
        },
    }

    inputs = {
        'code': code,
        'structure': structure,
        'pseudo_family': Str(pseudo_family),
        'kpoints': kpoints,
        'parameters': ParameterData(dict=parameters),
    }

    if automatic_parallelization:
        parallelization = {
            'max_num_machines': max_num_machines,
            'target_time_seconds': 0.5 * max_wallclock_seconds,
            'max_wallclock_seconds': max_wallclock_seconds
        }
        inputs['automatic_parallelization'] = ParameterData(dict=parallelization)
    else:
        options = get_default_options(max_num_machines, max_wallclock_seconds)
        inputs['options'] = ParameterData(dict=options)

    if clean_workdir:
        inputs['clean_workdir'] = Bool(True)

    if final_scf:
        inputs['final_scf'] = Bool(True)

    if group:
        inputs['group'] = Str(group)

    if daemon:
        workchain = submit(PwRelaxWorkChain, **inputs)
        click.echo('Submitted {}<{}> to the daemon'.format(PwRelaxWorkChain.__name__, workchain.pk))
    else:
        run(PwRelaxWorkChain, **inputs)