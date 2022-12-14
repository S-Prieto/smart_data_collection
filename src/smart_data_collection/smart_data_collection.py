#!/usr/bin/env python

import threading
import rospy
import actionlib
from smach import State,StateMachine
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from geometry_msgs.msg import PoseWithCovarianceStamped, PoseArray ,PointStamped
from std_msgs.msg import Empty
from std_srvs.srv import Trigger, TriggerResponse
from tf import TransformListener
import tf
import math
import rospkg
import csv
import time
from geometry_msgs.msg import PoseStamped
from blk360.srv import *

# TO DO: Create a state for calling the action client move_base (goal and result callback?)
# TO DO: Integrate Smach Viewer (create an Instrospection Server)
# TO DO: Create a state for scanning (ServiceState)

with_blk = True
scanned = False
trigger = True
counter = 0
number_of_scans = 1

def start_scan_response():
    global scanned
    global trigger
    global counter
    global number_of_scans

    if with_blk == True:
        rospy.wait_for_service('/start_measurement') # SCANNING SERVICE
    try:
        if with_blk == True:
            start_scan_srv = rospy.ServiceProxy('/start_measurement', startMeasurement) # SCANNING SERVICE
            resp1 = start_scan_srv(True, True, True, 'LDR', 'low', 'uncolorize') # SCANNING SERVICE
        else:
            rospy.loginfo("Simulating a BLK360 scan")
            rospy.sleep(2)

        rospy.loginfo("Service finished")
        rospy.loginfo("Counter = %s" % counter)
        counter = counter + 1
        if counter >= number_of_scans:
            rospy.loginfo("We set scanned to true")
            rospy.loginfo("Number of scans= %s" % number_of_scans)
            rospy.loginfo("Counter= %s" % counter)
            scanned = True

        trigger = False
        if with_blk == True:
            return resp1.success
        else:
            return True
    except rospy.ServiceException as e:
        print("Service call failed")

def trigger_response(request):
    global trigger
    trigger = False
    return TriggerResponse(success = True, message="Scan done")

# Change Pose to the correct frame
def changePose(waypoint,target_frame):
    if waypoint.header.frame_id == target_frame:
        # Already in correct frame
        return waypoint
    if not hasattr(changePose, 'listener'):
        changePose.listener = tf.TransformListener()
    tmp = PoseStamped()
    tmp.header.frame_id = waypoint.header.frame_id
    tmp.pose = waypoint.pose.pose
    try:
        changePose.listener.waitForTransform(
            target_frame, tmp.header.frame_id, rospy.Time(0), rospy.Duration(3.0))
        pose = changePose.listener.transformPose(target_frame, tmp)
        ret = PoseWithCovarianceStamped()
        ret.header.frame_id = target_frame
        ret.pose.pose = pose.pose
        return ret
    except:
        rospy.loginfo("CAN'T TRANSFORM POSE TO {} FRAME".format(target_frame))
        exit()


# Path for saving and retreiving the scan_positions.csv file
output_file_path = rospkg.RosPack().get_path('smart_data_collection')+"/saved_path/scan_positions.csv"
waypoints = []

def convert_PoseWithCovArray_to_PoseArray(waypoints):
    """Used to publish waypoints as pose array so that you can see them in Rviz, etc."""
    poses = PoseArray()
    poses.header.frame_id = rospy.get_param('~goal_frame_id','robot_map')
    poses.poses = [pose.pose.pose for pose in waypoints]
    return poses

class GetPath(State):
    def __init__(self):
        State.__init__(self, outcomes=['success'], input_keys=['waypoints'], output_keys=['waypoints'])
        # Subscribe to pose message to get new waypoints. TO DO: change the topic (hack into Rviz?)
        self.addpose_topic = rospy.get_param('~addpose_topic','/initialpose')
        # Create publisher to publish waypoints as pose array so that you can see them in Rviz, etc.
        self.posearray_topic = rospy.get_param('~posearray_topic','/waypoints')
        self.poseArray_publisher = rospy.Publisher(self.posearray_topic, PoseArray, queue_size=1)

        # Start thread to listen for reset messages to clear the waypoint queue
        def wait_for_path_reset():
            """Thread worker function"""
            global waypoints
            while not rospy.is_shutdown():
                data = rospy.wait_for_message('/path_reset', Empty)
                rospy.loginfo('Recieved path RESET message')
                self.initialize_path_queue()
                rospy.sleep(3) # Wait 3 seconds because `rostopic echo` latches
                               # for three seconds and wait_for_message() in a
                               # loop will see it again.
        reset_thread = threading.Thread(target=wait_for_path_reset)
        reset_thread.start()

    def initialize_path_queue(self):
        global waypoints
        waypoints = [] # The waypoint queue
        # Publish empty waypoint queue as pose array so that you can see them in Rviz, etc.
        self.poseArray_publisher.publish(convert_PoseWithCovArray_to_PoseArray(waypoints))

    def execute(self, userdata):
        global waypoints
        self.initialize_path_queue()
        self.path_ready = False

        # Start thread to listen for when the path is ready (this function will end then)
        # It will also save the clicked path to scan_positions.csv file
        def wait_for_path_ready():
            """thread worker function"""
            data = rospy.wait_for_message('/path_ready', Empty)
            rospy.loginfo('Recieved path READY message')
            self.path_ready = True
            with open(output_file_path, 'w') as file:
                for current_pose in waypoints:
                    file.write(str(current_pose.pose.pose.position.x) + ',' + str(current_pose.pose.pose.position.y) + ',' + str(current_pose.pose.pose.position.z) + ',' + str(current_pose.pose.pose.orientation.x) + ',' + str(current_pose.pose.pose.orientation.y) + ',' + str(current_pose.pose.pose.orientation.z) + ',' + str(current_pose.pose.pose.orientation.w)+ '\n')
            rospy.loginfo('Positions written to '+ output_file_path)
        ready_thread = threading.Thread(target=wait_for_path_ready)
        ready_thread.start()

        self.start_journey_bool = False

        # Start thread to listen start_journey
        # for loading the saved poses from smart_data_collection/saved_path/poses.csv
        def wait_for_start_journey():
            """thread worker function"""
            data_from_start_journey = rospy.wait_for_message('start_journey', Empty)
            rospy.loginfo('Recieved path READY start_journey')
            with open(output_file_path, 'r') as file:
                reader = csv.reader(file, delimiter = ',')
                for row in reader:
                    print (row)
                    current_pose = PoseWithCovarianceStamped()
                    current_pose.pose.pose.position.x     =    float(row[0])
                    current_pose.pose.pose.position.y     =    float(row[1])
                    current_pose.pose.pose.position.z     =    float(row[2])
                    current_pose.pose.pose.orientation.x = float(row[3])
                    current_pose.pose.pose.orientation.y = float(row[4])
                    current_pose.pose.pose.orientation.z = float(row[5])
                    current_pose.pose.pose.orientation.w = float(row[6])
                    waypoints.append(current_pose)
                    self.poseArray_publisher.publish(convert_PoseWithCovArray_to_PoseArray(waypoints))
            self.start_journey_bool = True
        start_journey_thread = threading.Thread(target=wait_for_start_journey)
        start_journey_thread.start()

        topic = self.addpose_topic;
        rospy.loginfo("Waiting to recieve waypoints via Pose msg on topic %s" % topic)
        rospy.loginfo("To start following waypoints: 'rostopic pub -1 /path_ready std_msgs/Empty'")
        rospy.loginfo("OR")
        rospy.loginfo("To start following saved waypoints: 'rostopic pub -1 /start_journey std_msgs/Empty'")


        # Wait for published waypoints or saved path loaded
        while (not self.path_ready and not self.start_journey_bool):
            try:
                pose = rospy.wait_for_message(topic, PoseWithCovarianceStamped, timeout=1)
            except rospy.ROSException as e:
                if 'timeout exceeded' in e.message:
                    continue  # no new waypoint within timeout, looping...
                else:
                    raise e
            rospy.loginfo("Recieved new waypoint")
            waypoints.append(changePose(pose, "robot_map"))
            # Publish waypoint queue as pose array so that you can see them in Rviz, etc.
            self.poseArray_publisher.publish(convert_PoseWithCovArray_to_PoseArray(waypoints))

        # Path is ready! return success and move on to the next state (FOLLOW_PATH)
        return 'success'

class FollowPath(State):
    def __init__(self):
        State.__init__(self, outcomes=['success'], input_keys=['waypoints'])
        self.frame_id = rospy.get_param('~goal_frame_id','robot_map')
        self.odom_frame_id = rospy.get_param('~odom_frame_id','robot_odom')
        self.base_frame_id = rospy.get_param('~base_frame_id','robot_base_footprint')
        self.duration = rospy.get_param('~wait_duration', 0.0)
        # Get a move_base action client
        self.client = actionlib.SimpleActionClient('move_base', MoveBaseAction)
        rospy.loginfo('Connecting to move_base...')
        self.client.wait_for_server()
        rospy.loginfo('Connected to move_base.')
        rospy.loginfo('Starting a tf listener.')
        self.tf = TransformListener()
        self.listener = tf.TransformListener()
        self.distance_tolerance = rospy.get_param('waypoint_distance_tolerance', 0.0)

    def execute(self, userdata):
        global waypoints
        global trigger
        global scanned
        global counter
        global number_of_scans
        
        scanned = False
        # Execute waypoints each in sequence
        for waypoint in waypoints:

            
            trigger = True
            # Break if preempted
            if waypoints == []:
                rospy.loginfo('The waypoint queue has been reset.')
                break
            # Otherwise publish next waypoint as goal
            goal = MoveBaseGoal()
            goal.target_pose.header.frame_id = self.frame_id
            goal.target_pose.pose.position = waypoint.pose.pose.position
            goal.target_pose.pose.orientation = waypoint.pose.pose.orientation
            rospy.loginfo('Executing move_base goal to position (x,y): %s, %s' %
                    (waypoint.pose.pose.position.x, waypoint.pose.pose.position.y))
            rospy.loginfo("To cancel the goal: 'rostopic pub -1 /move_base/cancel actionlib_msgs/GoalID -- {}'")
            self.client.send_goal(goal)
            if not self.distance_tolerance > 0.0:
                self.client.wait_for_result()
                while trigger == True:

                    if scanned == False:
                        rospy.loginfo("Scan starting!")
                        rospy.loginfo("Calling service_debug_1")
                        start_scan_response()
                        rospy.loginfo("Sucessfully scanned, moving to the next position.")
                        # scanned = True
                    else: 
                        rospy.loginfo("Not scanning, waiting two seconds...")
                        time.sleep(2)
                        trigger = False
                        #rospy.loginfo("Moving to the next position")

                    time.sleep(1)

                # scanning = True
                # resp1 = start_scan_srv(True, False, False, 'low')
                # if resp1.success:
                #     rospy.loginfo('Sucessfully scanned, moving to the next position.')
                # else:
                #     rospy.loginfo('Something went wrong with the scanning')

                rospy.loginfo("Waiting for %f sec..." % self.duration)
                time.sleep(self.duration)
            else:
                #This is the loop which exist when the robot is near a certain GOAL point.
                distance = 10
                while(distance > self.distance_tolerance):
                    now = rospy.Time.now()
                    self.listener.waitForTransform(self.odom_frame_id, self.base_frame_id, now, rospy.Duration(4.0))
                    trans,rot = self.listener.lookupTransform(self.odom_frame_id,self.base_frame_id, now)
                    distance = math.sqrt(pow(waypoint.pose.pose.position.x-trans[0],2)+pow(waypoint.pose.pose.position.y-trans[1],2))
        return 'success'

class PathComplete(State):
    def __init__(self):
        State.__init__(self, outcomes=['success'])

    def execute(self, userdata):
        rospy.loginfo('###############################')
        rospy.loginfo('##### REACHED FINISH GOAL #####')
        rospy.loginfo('###############################')
        return 'success'

def main():

    rospy.init_node('smart_data_collection')

    trigger_srv = rospy.Service('/scan_trigger', Trigger, trigger_response)
    rospy.loginfo('Waiting for scanning service...')
    if with_blk == True:
        rospy.wait_for_service('/start_measurement') # SCAN SERVICE
        start_scan_srv = rospy.ServiceProxy('/start_measurement=', startMeasurement) # SCAN SERVICE

    rospy.loginfo("Service connected!")

    sm = StateMachine(outcomes=['success'])

    with sm:
        StateMachine.add('GET_PATH', GetPath(),
                           transitions={'success':'FOLLOW_PATH'},
                           remapping={'waypoints':'waypoints'})
        StateMachine.add('FOLLOW_PATH', FollowPath(),
                           transitions={'success':'PATH_COMPLETE'},
                           remapping={'waypoints':'waypoints'})
        StateMachine.add('PATH_COMPLETE', PathComplete(),
                           transitions={'success':'GET_PATH'})

    outcome = sm.execute()
