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

    with open('results.csv', 'w') as f:
        ybd.app.log('results', 'Writing results to', 'results.csv')
        write_csv_file(
            f,
            rows=results.values(),
            columns=['name', 'sloc', 'git_active_days', 'git_active_days_per_author', 'git_authors', 'ref_name'])


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
        extract_commit(name, repo, ref, source_dir)

        try:
            sloc = sloccount_physical_source_lines_of_code(name, source_dir)
        except subprocess.CalledProcessError as e:
            ybd.app.log(name, "Error running sloccount", e)
            sloc = -1

        gitdir = os.path.join(ybd.app.config['gits'],
                              ybd.repos.get_repo_name(repo))
        active_days = git_active_days(gitdir, ref)

        active_days_per_author = git_active_days(gitdir, ref, per_author=True)

        authors = git_count_authors(gitdir, ref)

        return {
            'name': name,
            'sloc': sloc,
            'git_active_days': active_days,
            'git_active_days_per_author': active_days_per_author,
            'git_authors': authors,
        }
    finally:
        if os.path.exists(source_dir):
            shutil.rmtree(source_dir)


def sloccount_physical_source_lines_of_code(name, source_dir):
    '''Use 'sloccount' to measure the physical source lines of code.'''

    # There's no 'machine-readable' output feature of 'sloccount' that we could
    # use, we just match against a string in the output.

    ybd.app.log(name, "Counting lines of code with sloccount in", source_dir)
    text = subprocess.check_output(['sloccount', source_dir])

    for line in text.decode('ascii').splitlines():
        if line.startswith('Total Physical Source Lines of Code (SLOC)'):
            number_string = line.split()[-1]
            number_string = number_string.replace(',', '')
            return int(number_string)

    raise RuntimeError("Unexpected output from sloccount: %s" % text)


def git_active_days(gitdir, ref, per_author=False):
    '''Use 'git-active-days' to measure activity in a Git repository.

    The 'git-active-days' script lives alongside this one.

    '''
    script = os.path.join(os.path.dirname(__file__), 'git-active-days')

    args = ['--ref', ref]
    if per_author:
        args += ['--per-author']

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
        ybd.repos.mirror(name, repo)
    elif not ybd.repos.mirror_has_ref(gitdir, ref):
        ybd.repos.update_mirror(name, repo, gitdir)

    with tempfile.NamedTemporaryFile() as git_index_file:
        git_env = os.environ.copy()
        git_env['GIT_INDEX_FILE'] = git_index_file.name
        git_env['GIT_WORK_TREE'] = target_dir

        ybd.app.log(repo, 'Extracting commit', ref)
        subprocess.check_call(
            ['git', 'read-tree', ref], env=git_env, cwd=gitdir)
        subprocess.check_call(
            ['git', 'checkout-index', '--all'], env=git_env, cwd=gitdir)


try:
    main()
except RuntimeError as e:
    sys.stderr.write('ERROR: %s\n' % e)
    sys.exit(1)
