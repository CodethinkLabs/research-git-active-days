# Copyright (C) 2015  Codethink Limited
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.

'''measure.py: Analyses source code of Baserock components and dependencies.

See README for instructions.

'''

import ybd

import os
import shutil
import subprocess
import sys
import tempfile


def main():
    if len(sys.argv) != 2:
        raise RuntimeError(
            "Please run this script in a Baserock definitions tree, and pass "
            "the path to a definition file as an argument.")

    target_filename = sys.argv[1]

    definitions = load_ybd_definitions(target_filename)

    target = definitions.get(target_filename)

    if target is None:
        raise RuntimeError("Did not find component '%s'" % target_filename)

    # Now we need to get the build graph for 'target', and get all
    # the repos that are involved.
    #
    # The list is reversed because otherwise gcc and binutils end up first,
    # and they are the biggest repos :-)
    components = reversed(list(walk_dependencies(definitions, target)))

    try:
        results = {}
        for c in components:
            if 'repo' in c and 'ref' in c:
                repo = c['repo']
                ref = c['ref']
                key = (repo, ref)
                if key not in results:
                    stats = measure_component_source_repo(
                        c['name'], repo, ref)
                    results[key] = stats

                    ref_name = c.get('unpetrify-ref', ref)
                    stats['ref_name'] = ref_name
    except KeyboardInterrupt:
        # It's nice when developing the script to be able to ctrl+c and still
        # get some results.
        pass
    except:
        # It's nice to get some results even if something goes wrong
        ybd.app.log(c, 'ERROR during analysis - results are incomplete')
        pass


    with open('results.csv', 'w') as f:
        ybd.app.log('RESULTS', 'LOC generated using',
                    "David A. Wheeler's 'SLOCCount'")
        ybd.app.log('RESULTS', 'Writing results to', 'results.csv')
        rows = results.values()
        columns = sorted(rows[0].keys())
        write_csv_file(
            f, rows=results.values(), columns=columns)


def load_ybd_definitions(target_filename):
    '''Initialise YBD and load all definitions from current working directory.

    Returns a ybd Definitions instance.

    '''
    ybd.app.setup(args=[__file__, target_filename])

    definitions = ybd.definitions.Definitions()
    return definitions


def measure_component_source_repo(name, repo, ref):
    '''Analyse a single component. Returns a dict of results.'''

    # Don't do this in /tmp, because on an OpenStack VM's root disk it will
    # take FOREVER.
    source_dir = tempfile.mkdtemp(dir=ybd.app.config['tmp'])
    try:
#        extract_commit(name, repo, ref, source_dir)
        ybd.repos.checkout(name, repo, ref, source_dir)

        try:
            sloc = sloccount_physical_source_lines_of_code(name, source_dir)
        except subprocess.CalledProcessError as e:
            ybd.app.log(name, "Error running sloccount", e)
            sloc = -1

        gitdir = os.path.join(ybd.app.config['gits'],
                              ybd.repos.get_repo_name(repo))

        all_refs_active_days = git_active_days(
            gitdir)

        all_refs_active_person_days = git_active_days(
            gitdir, person_days=True)

        ref_active_days = git_active_days(gitdir, ref=ref)

        ref_active_person_days = git_active_days(
            gitdir, ref=ref, person_days=True)

        authors = git_count_authors(gitdir, ref)

        return {
            'name': name,
            'sloc': sloc,
            'git_active_days_all_refs': all_refs_active_days,
            'git_active_days_for_ref': ref_active_days,
            'git_active_person_days_all_refs': all_refs_active_person_days,
            'git_active_person_days_for_ref': ref_active_person_days,
            'git_authors': authors,
        }
    finally:
        if os.path.exists(source_dir):
            shutil.rmtree(source_dir)


def sloccount_physical_source_lines_of_code(name, source_dir):
    '''Use 'sloccount' to measure the physical source lines of code.'''

    # There's no 'machine-readable' output feature of 'sloccount' that we could
    # use, we just match against a string in the output.

    ybd.app.log(name, "Counting code lines with sloccount in", source_dir)
    with open(os.devnull, "w") as fnull:
        text = subprocess.check_output(['sloccount', source_dir],
                                       stderr=fnull)
    for line in text.decode('unicode-escape').splitlines():
        if line.startswith('Development Effort Estimate'):
            print line
        if line.startswith('Total Physical Source Lines of Code (SLOC)'):
            print line
            number_string = line.split()[-1]
            number_string = number_string.replace(',', '')
    try:
        ybd.app.log(name, "Sloccount LOC is", number_string)
        return int(number_string)
    except:
        raise RuntimeError("Unexpected output from sloccount: %s" % text)


def git_active_days(gitdir, ref=None, person_days=False):
    '''Use 'git-active-days' to measure activity in a Git repository.

    If 'ref' is passed, days for just that ref are returned. Otherwise, all
    refs are included in the measurement.

    The 'git-active-days' script lives alongside this one.

    '''
    script = os.path.join(os.path.dirname(__file__), 'git-active-days')

    if ref:
        args = ['--ref', ref]
    else:
        args = ['--all-refs']

    if person_days:
        args += ['--person-days']

    text = subprocess.check_output([script] + args + [gitdir])

    return int(text.decode('ascii').strip())


def git_count_authors(gitdir, ref):
    '''Count the number of commit authors in a Git repo.'''

    text = subprocess.check_output(
        ['git', 'shortlog', '--email', '--summary', ref], cwd=gitdir)
    return len(text.splitlines())


def write_csv_file(f, rows, columns):
    '''Write data from 'rows' to stream 'f' as comma-separated values.

    This is a really naive implementation, which doesn't even handle escaping.
    It should probably be replaced with a 3rd-party library.

    '''
    f.write(', '.join(columns) + '\n')

    for item in rows:
        line = ', '.join(str(item[column]) for column in columns)
        f.write(line + '\n')


def walk_dependencies(definitions, component):
    '''Yields 'component' and all of its dependencies, in depth-first order.'''

    done = set()

    def walk(component):
        dependencies = component.get('build-depends', [])
        dependencies.extend(component.get('contents', []))
        for system in component.get('systems', []):
            dependencies.append(system['path'])

        for dep_path in dependencies:
            if dep_path not in done:
                done.add(dep_path)

                dep = definitions.get(dep_path)
                for child_dep in walk(dep):
                    yield child_dep
                yield dep

    return walk(component)


def extract_commit(name, repo, ref, target_dir):
    '''Check out a single commit (or tree) from a Git repo.

    The ybd.repos.checkout() function actually clones the entire repo, so this
    function is much quicker when you don't need to copy the whole repo into
    target_dir.

    '''
    gitdir = os.path.join(ybd.app.config['gits'],
                          ybd.repos.get_repo_name(repo))

    if not os.path.exists(gitdir):
        ybd.app.log(name, 'Cloning', repo)
        ybd.repos.mirror(name, repo)
    elif not ybd.repos.mirror_has_ref(gitdir, ref):
        ybd.app.log(name, 'Updating', repo)
        ybd.repos.update_mirror(name, repo, gitdir)

    with tempfile.NamedTemporaryFile() as git_index_file:
        git_env = os.environ.copy()
        git_env['GIT_INDEX_FILE'] = git_index_file.name
        git_env['GIT_WORK_TREE'] = target_dir

        ybd.app.log(name, 'Extracting commit', ref)
        subprocess.check_call(
            ['git', 'read-tree', ref], env=git_env, cwd=gitdir)
        subprocess.check_call(
            ['git', 'checkout-index', '--all'], env=git_env, cwd=gitdir)

try:
    main()
except RuntimeError as e:
    sys.stderr.write('ERROR: %s\n' % e)
    sys.exit(1)
